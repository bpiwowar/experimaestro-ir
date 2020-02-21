from experimaestro import argument, task
import experimaestro_ir as ir
from datamaestro_text.data.trec import TrecAdhocAssessments, TrecAdhocResults

import logging
import pytrec_eval


@argument("assessments", TrecAdhocAssessments)
@argument("results", TrecAdhocResults)
@task(ir.NS.evaluate.trec)
def TrecEval(assessments, results):
    """Evaluate an IR ad-hoc run with trec-eval"""
    logging.info("Reading assessments %s", assessments.path)
    with assessments.path.open("r") as f_qrel:
        qrel = pytrec_eval.parse_qrel(f_qrel)

    logging.info("Reading results %s", results.results)
    with results.results.open("r") as f_run:
        run = pytrec_eval.parse_run(f_run)

    evaluator = pytrec_eval.RelevanceEvaluator(qrel, pytrec_eval.supported_measures)
    results = evaluator.evaluate(run)

    def print_line(measure, scope, value):
        print("{:25s}{:8s}{:.4f}".format(measure, scope, value))

    for query_id, query_measures in sorted(results.items()):
        for measure, value in sorted(query_measures.items()):
            print_line(measure, query_id, value)

    # Scope hack: use query_measures of last item in previous loop to
    # figure out all unique measure names.
    #
    # TODO(cvangysel): add member to RelevanceEvaluator
    #                  with a list of measure names.
    for measure in sorted(query_measures.keys()):
        print_line(
            measure,
            "all",
            pytrec_eval.compute_aggregated_measure(
                measure,
                [query_measures[measure] for query_measures in results.values()],
            ),
        )
