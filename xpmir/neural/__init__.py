from typing import List
import torch
import torch.nn as nn

from experimaestro import config, Param
from xpmir.letor.samplers import Records, SamplerRecord
from xpmir.rankers import LearnableScorer, ScoredDocument
from xpmir.vocab import Vocab


@config()
class InteractionScorer(LearnableScorer, nn.Module):
    """A scorer based on token embeddings

    Attributes:
        vocab: The embedding model -- the vocab also defines how to tokenize text
        qlen: Maximum query length (this can be even shortened by the model)
        dlen: Maximum document length (this can be even shortened by the model)
        add_runscore:
            Whether the base predictor score should be added to the
            model score
    """

    vocab: Param[Vocab]
    qlen: Param[int] = 20
    dlen: Param[int] = 2000
    add_runscore: Param[bool] = False

    def initialize(self, random):
        self.random = random
        seed = self.random.randint((2 ** 32) - 1)
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
        if self.add_runscore:
            self.runscore_alpha = torch.nn.Parameter(torch.full((1,), -1.0))
        self.vocab.initialize()

    def rsv(self, query: str, documents: List[ScoredDocument]) -> List[ScoredDocument]:
        # Prepare the inputs and call the model
        inputs = Records()
        for doc in documents:
            assert doc.content is not None
            inputs.add(SamplerRecord(query, doc.docid, doc.content, doc.score, None))

        with torch.no_grad():
            scores = self(inputs).cpu().numpy()

        # Returns the scored documents
        scoredDocuments = []
        for i in range(len(documents)):
            scoredDocuments.append(ScoredDocument(documents[i].docid, scores[i]))

        return scoredDocuments

    def __validate__(self):
        assert (
            self.dlen < self.vocab.maxtokens()
        ), f"The maximum document length ({self.dlen}) should be less that what the vocab can process ({self.vocab.maxtokens})"
        assert (
            self.qlen < self.vocab.maxtokens()
        ), f"The maximum query length ({self.qlen}) should be less that what the vocab can process ({self.vocab.maxtokens})"

    def forward(self, inputs: Records):
        # Forward to model
        result = self._forward(inputs)

        if len(result.shape) == 2 and result.shape[1] == 1:
            result = result.reshape(result.shape[0])

        # Add run score if needed
        if self.add_runscore:
            alpha = torch.sigmoid(self.runscore_alpha)
            result = alpha * result + (1 - alpha) * inputs.scores

        return result

    def _forward(self, inputs: Records):
        raise NotImplementedError

    def save(self, path):
        state = self.state_dict(keep_vars=True)
        for key in list(state):
            if state[key].requires_grad:
                state[key] = state[key].data
            else:
                del state[key]
        torch.save(state, path)

    def load(self, path):
        self.load_state_dict(torch.load(path), strict=False)
