"""The retrieval side in LlamaIndex, over the same chunks and the same embedding model.

Our chunks become TextNodes (node_id = chunk id), and our embedder is wrapped as a
LlamaIndex embedding model, so the only thing that changes is the framework. Run the same
labelled questions through both and compare. No LLM is configured, because retrieval
doesn't need one (and LlamaIndex would otherwise default to a provider we don't use).
"""
from __future__ import annotations

from typing import Any

from llama_index.core import VectorStoreIndex
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.schema import TextNode

from fieldcopilot.rag.chunking import Chunk
from fieldcopilot.rag.embeddings import Embedder


class WrappedEmbedding(BaseEmbedding):
    """Our embedder, presented as a LlamaIndex embedding model."""

    def __init__(self, embedder: Embedder, **kwargs: Any):
        super().__init__(model_name=embedder.name, embed_batch_size=64, **kwargs)
        self._embedder = embedder

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._embedder.embed_query(query).tolist()

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._embedder.embed_documents([text])[0].tolist()

    def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        return self._embedder.embed_documents(texts).tolist()

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._get_query_embedding(query)


class LlamaIndexRetriever:
    """Same `search(query, k)` shape as our Retriever, so the evaluation can run both."""

    name = "llamaindex-vector"

    def __init__(self, chunks: list[Chunk], embedder: Embedder):
        self._by_id = {c.id: c for c in chunks}
        nodes = [TextNode(id_=c.id, text=c.index_text, metadata={"doc_id": c.doc_id, "tenant": c.tenant}) for c in chunks]
        self.index = VectorStoreIndex(nodes, embed_model=WrappedEmbedding(embedder), show_progress=False)

    def search(self, query: str, k: int = 5):
        from fieldcopilot.rag.retriever import Hit

        results = self.index.as_retriever(similarity_top_k=k).retrieve(query)
        return [Hit(self._by_id[r.node.node_id], float(r.score or 0.0)) for r in results]
