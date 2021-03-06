[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![Documentation Status](https://readthedocs.org/projects/experimaestro-ir/badge/?version=latest)](https://experimaestro-ir.readthedocs.io/en/latest/?badge=latest)

# Information Retrieval for experimaestro

Information Retrieval module for [experimaestro](https://experimaestro.github.io/experimaestro-python/)

The full documentation can be read at [IR@experimaestro](https://experimaestro-ir.readthedocs.io/).

## Install

Base experimaestro-IR can be installed with `pip install xpmir`.
Functionalities can be added by installing optional dependencies:

- `pip install xpmir[neural]` to install neural-IR packages
- `pip install xpmir[anserini]` to install Anserini related packages


## What's inside?

- Collection management (using datamaestro)
- Indexing and retrieval with anserini
- Neural IR:
    - DRMM
