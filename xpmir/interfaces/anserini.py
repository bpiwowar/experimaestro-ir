import asyncio
import contextlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from typing import List

import datamaestro_text.data.ir.csv as ir_csv
from datamaestro_text.data.ir.trec import (
    AdhocDocuments,
    AdhocTopics,
    TipsterCollection,
    TrecAdhocTopics,
)
from experimaestro import config, param, pathoption, progress, task
from tqdm import tqdm
from xpmir.dm.data.anserini import Index
from xpmir.evaluation import TrecAdhocRun
from xpmir.rankers import Retriever, ScoredDocument
from xpmir.rankers.standard import BM25, Model
from xpmir.utils import Handler


def javacommand():
    """Returns the start of the java command including the Anserini class path"""
    from jnius_config import get_classpath
    from pyserini.pyclass import configure_classpath

    command = ["{}/bin/java".format(os.environ["JAVA_HOME"]), "-cp"]
    command.append(":".join(get_classpath()))

    return command


class StreamGenerator(Thread):
    def __init__(self, generator, mode="wb"):
        super().__init__()
        tmpdir = tempfile.mkdtemp()
        self.mode = mode
        self.filepath = Path(os.path.join(tmpdir, "fifo.json"))
        os.mkfifo(self.filepath)
        subprocess.run(["find", tmpdir])
        self.generator = generator
        self.thread = None

    def run(self):
        with self.filepath.open(self.mode) as out:
            self.generator(out)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.join()
        self.filepath.unlink()
        self.filepath.parent.rmdir()


@param("documents", type=AdhocDocuments)
@param("threads", default=8, ignored=True)
@pathoption("path", "index")
@task(description="Index a documents")
class IndexCollection(Index):
    """An [Anserini](https://github.com/castorini/anserini) index"""

    CLASSPATH = "io.anserini.index.IndexCollection"

    def execute(self):
        command = javacommand()
        command.append(IndexCollection.CLASSPATH)
        command.extend(["-index", self.path, "-threads", self.threads])

        chandler = Handler()

        @chandler()
        def trec_collection(documents: TipsterCollection):
            return contextlib.nullcontext("void"), [
                "-collection",
                "TrecCollection",
                "-input",
                documents.path,
            ]

        @chandler()
        def csv_collection(documents: ir_csv.AdhocDocuments):
            def _generator(out):
                counter = 0
                size = os.path.getsize(documents.path)
                with documents.path.open("rt", encoding="utf-8") as fp, tqdm(
                    total=size, unit="B", unit_scale=True
                ) as pb:
                    for ix, line in enumerate(fp):
                        # Update progress (TODO: cleanup/factorize the progress code)
                        ll = len(line)
                        pb.update(ll)
                        counter += ll
                        progress(counter / size)

                        # Generate document
                        docid, text = line.strip().split(documents.separator, 1)
                        json.dump({"id": docid, "contents": text}, out)
                        out.write("\n")

            generator = StreamGenerator(_generator, mode="wt")

            return generator, [
                "-collection",
                "JsonCollection",
                "-input",
                generator.filepath.parent,
            ]

        generator, args = chandler[self.documents]
        command.extend(args)

        if self.storePositions:
            command.append("-storePositions")
        if self.storeDocvectors:
            command.append("-storeDocvectors")
        if self.storeRaw:
            command.append("-storeRawDocs")
        if self.storeContents:
            command.append("-storeContents")

        print("Running", command)
        # Index and keep track of progress through regular expressions
        RE_FILES = re.compile(
            rb""".*index\.IndexCollection \(IndexCollection.java:\d+\) - ([\d,]+) files found"""
        )
        RE_FILE = re.compile(
            rb""".*index\.IndexCollection\$LocalIndexerThread \(IndexCollection.java:\d+\).* docs added."""
        )
        RE_COMPLETE = re.compile(
            rb""".*IndexCollection\.java.*Indexing Complete.*documents indexed"""
        )

        async def run(command):
            with generator as yo:
                proc = await asyncio.create_subprocess_exec(
                    *command, stderr=None, stdout=asyncio.subprocess.PIPE
                )

                nfiles = -1
                indexedfiles = 0
                complete = False

                while True:
                    data = await proc.stdout.readline()

                    if not data:
                        break

                    m = RE_FILES.match(data)
                    complete = complete or (RE_COMPLETE.match(data) is not None)
                    if m:
                        nfiles = int(m.group(1).decode("utf-8").replace(",", ""))
                        print("%d files to index" % nfiles)
                    elif RE_FILE.match(data):
                        indexedfiles += 1
                        progress(indexedfiles / nfiles)
                    else:
                        sys.stdout.write(
                            data.decode("utf-8"),
                        )

                await proc.wait()

                if proc.returncode == 0 and not complete:
                    logging.error(
                        "Did not see the indexing complete log message -- exiting with error"
                    )
                    sys.exit(1)
                sys.exit(proc.returncode)

        asyncio.run(run([str(s) for s in command]))


@param("index", Index)
@param("topics", AdhocTopics)
@param("model", Model)
@pathoption("path", "results.trec")
@task()
class SearchCollection:
    def execute(self):
        command = javacommand()
        command.append("io.anserini.search.SearchCollection")
        command.extend(("-index", self.index.path, "-output", self.path))

        # Topics

        topicshandler = Handler()

        @topicshandler()
        def trectopics(topics: TrecAdhocTopics):
            return ("-topicreader", "Trec", "-topics", topics.path)

        @topicshandler()
        def tsvtopics(topics: ir_csv.AdhocTopics):
            return ("-topicreader", "TsvInt", "-topics", topics.path)

        command.extend(topicshandler[self.topics])

        # Model

        modelhandler = Handler()

        @modelhandler()
        def handle(bm25: BM25):
            return ("-bm25", "-bm25.k1", str(bm25.k1), "-bm25.b", str(bm25.b))

        command.extend(modelhandler[self.model])

        # Start
        logging.info("Starting command %s", command)
        p = subprocess.run(command)
        sys.exit(p.returncode)


@param("index", Index, help="Anserini index")
@param("model", Model, help="Model used to search")
@param("k", default=1500, help="Number of results to retrieve")
@config()
class AnseriniRetriever(Retriever):
    def initialize(self):
        from pyserini.search import SimpleSearcher

        self.searcher = SimpleSearcher(str(self.index.path))

        modelhandler = Handler()

        @modelhandler()
        def handle(bm25: BM25):
            self.searcher.set_bm25(bm25.k1, bm25.b)

        modelhandler[self.model]

    def retrieve(self, query: str) -> List[ScoredDocument]:
        hits = self.searcher.search(query, k=self.k)
        return [ScoredDocument(hit.docid, hit.score, hit.contents) for hit in hits]
