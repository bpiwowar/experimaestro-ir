import math
from experimaestro import param, config, Choices
import torch
from torch import nn
from xpmir.dm.data.base import Index
from . import InteractionScorer
import xpmir.neural.modules as modules


@param("nbins", default=29, help="number of bins in matching histogram")
@config()
class CountHistogram(nn.Module):
    def forward(self, simmat, dlens, dtoks, qtoks):
        BATCH, CHANNELS, QLEN, DLEN = simmat.shape

        # +1e-5 to nudge scores of 1 to above threshold
        bins = ((simmat + 1.00001) / 2.0 * (self.nbins - 1)).int()
        weights = (
            (dtoks != -1).reshape(BATCH, 1, DLEN).expand(BATCH, QLEN, DLEN)
            * (qtoks != -1).reshape(BATCH, QLEN, 1).expand(BATCH, QLEN, DLEN)
        ).float()
        # apparently no way to batch this... https://discuss.pytorch.org/t/histogram-function-in-pytorch/5350
        bins, weights = (
            bins.cpu(),
            weights.cpu(),
        )  # WARNING: this line (and the similar line below) improve performance tenfold when on GPU
        histogram = []
        for superbins, w in zip(bins, weights):
            result = []
            for b in superbins:
                result.append(
                    torch.stack(
                        [torch.bincount(q, x, self.nbins) for q, x in zip(b, w)], dim=0
                    )
                )
            result = torch.stack(result, dim=0)
            histogram.append(result)
        histogram = torch.stack(histogram, dim=0)
        histogram = histogram.to(
            simmat.device
        )  # WARNING: this line (and the similar line above) improve performance tenfold when on GPU
        return histogram


@config()
class NormalizedHistogram(CountHistogram):
    def forward(self, simmat, dlens, dtoks, qtoks):
        result = super().forward(simmat, dlens, dtoks, qtoks)
        BATCH, QLEN, _ = simmat.shape
        return result / dlens.reshape(BATCH, 1).expand(BATCH, QLEN)


@config()
class LogCountHistogram(CountHistogram):
    def forward(self, simmat, dlens, dtoks, qtoks):
        result = super().forward(simmat, dlens, dtoks, qtoks)
        return (result.float() + 1e-5).log()


@param(
    "hidden", default=5, help="hidden layer dimension for feed forward matching network"
)
@param("hist", type=CountHistogram, default=LogCountHistogram(), help="Histogram")
@param("combine", default="idf", checker=Choices(["idf", "sum"]), help="term gate type")
@param(
    "index", type=Index, required=False, help="The index when computing the with IDF"
)
@config()
class Drmm(InteractionScorer):
    """
    Implementation of the DRMM model from:
      > Jiafeng Guo, Yixing Fan, Qingyao Ai, and William Bruce Croft. 2016. A Deep Relevance
      > Matching Model for Ad-hoc Retrieval. In CIKM.
    """

    def __validate__(self):
        super().__validate__()
        assert (self.combine != "idf") or (
            self.index is not None
        ), "index must be provided if using IDF"

    def initialize(self, random):
        super().initialize(random)

        if not self.vocab.static():
            self.logger.warn(
                "In most cases, using vocab.train=True will not have an effect on DRMM "
                "because the histogram is not differentiable. An exception might be if "
                "the gradient is proped back by another means, e.g. BERT [CLS] token."
            )
        self.simmat = modules.InteractionMatrix()
        channels = self.vocab.emb_views()
        self.hidden_1 = nn.Linear(self.hist.nbins * channels, self.hidden)
        self.hidden_2 = nn.Linear(self.hidden, 1)
        self.needs_idf = self.combine == "idf"
        self.combine = {"idf": IdfCombination, "sum": SumCombination}[self.combine]()

    def _forward(self, inputs):
        simmat = self.simmat.encode_query_doc(self.vocab, inputs)

        if self.needs_idf:
            inputs.query_idf = torch.full_like(
                inputs.queries_tokids, float("-inf"), dtype=torch.float
            )
            log_nd = math.log(self.index.documentcount + 1)
            for i, tok in enumerate(inputs.queries_toks):
                for j, t in enumerate(tok):
                    inputs.query_idf[i, j] = log_nd - math.log(
                        self.index.term_df(t) + 1
                    )

        qterm_features = self.histogram_pool(simmat, inputs)
        BAT, QLEN, _ = qterm_features.shape
        qterm_scores = self.hidden_2(torch.relu(self.hidden_1(qterm_features))).reshape(
            BAT, QLEN
        )
        return self.combine(qterm_scores, inputs.query_idf)

    def histogram_pool(self, simmat, inputs):
        histogram = self.hist(
            simmat, inputs.docs_len, inputs.docs_tokids, inputs.queries_tokids
        )
        BATCH, CHANNELS, QLEN, BINS = histogram.shape
        histogram = histogram.permute(0, 2, 3, 1)
        histogram = histogram.reshape(BATCH, QLEN, BINS * CHANNELS)
        return histogram


class SumCombination(nn.Module):
    def forward(self, scores, idf):
        return scores.sum(dim=1)


class IdfCombination(nn.Module):
    def forward(self, scores, idf):
        idf = idf.softmax(dim=1)
        return (scores * idf).sum(dim=1)
