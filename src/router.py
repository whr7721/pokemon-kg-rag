# -*- coding: utf-8 -*-
"""统一问答入口：结构化直答与 GraphRAG 都是策略，共用同一个图访问层。"""
from __future__ import annotations

from graph_access import GraphAccess
from multi_qa import MultiQA
from rag import PokemonGraphRAG


class RagRouter:
    def __init__(self):
        self.graph = GraphAccess()
        self.mqa = MultiQA(self.graph)
        self.rag = PokemonGraphRAG(self.graph)

    def answer(self, question, top_k=8, use_graph=True, retrieval_mode="hybrid_graph"):
        structured = self.mqa.answer(question)
        if structured is not None:
            evidence = self.rag.retrieve(question, top_k=top_k, mode=retrieval_mode)
            return {
                "question": question,
                "answer": structured["answer"],
                "mode": structured["kind"],
                "facts": [],
                "evidence": evidence,
                "retrieval_mode": retrieval_mode,
                "subgraph": self.rag.subgraph(evidence, limit=3),
            }

        result = self.rag.ask(question, top_k=top_k, use_graph=use_graph, retrieval_mode=retrieval_mode)
        return {
            "question": question,
            "answer": result["answer"],
            "mode": result.get("mode", "graph_rag"),
            "facts": result.get("facts", []),
            "evidence": result["evidence"],
            "retrieval_mode": result.get("retrieval_mode", retrieval_mode),
            "subgraph": self.rag.subgraph(result["evidence"], limit=3),
        }

    def health(self):
        return self.rag.health()

    def close(self):
        self.graph.close()
