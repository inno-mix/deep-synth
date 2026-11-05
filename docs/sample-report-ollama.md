# Technical Brief: Performance Comparison of PostgreSQL pgvector vs Qdrant for Vector Search

> **Topic:** Compare PostgreSQL pgvector vs Qdrant performance for vector search  
> **Generated:** 2026-11-05 20:16 UTC · **Model:** `ollama:qwen2.5:7b` · **Verified findings:** 8 · **Sources:** 6

## TL;DR

Qdrant outperforms pgvector in terms of latency and throughput at scale, but comes with higher costs. pgvector offers a cost-effective solution with flexible deployment options.

## At a glance

| Dimension | Qdrant | pgvector |
|---|---|---|
| Latency (p99) | 12ms | Not directly comparable, effective for scales below ten million vectors. |
| Throughput (RPS) | Up to 4x the RPS of open-source rivals | Effective for scales below ten million vectors |
| Cost | ~$65/mo for 10M vectors, billed per node, no per-query fee | $50–$180/mo on RDS (Postgres instance cost) |
| Payload Filtering | Yes: Metadata-based query-time filters without additional queries | No |

## Overview

This brief compares the performance characteristics of PostgreSQL pgvector and Qdrant for vector search applications. Key findings indicate that Qdrant excels in high-scale scenarios, while pgvector provides a cost-effective alternative with flexible deployment options.

## Performance

- **Qdrant**: Hits ~12ms p99 at 10M vectors and up to 4x the RPS of open-source rivals [[1]](#ref-1).
- **pgvector**: Effective for scales below ten million vectors, with HNSW and disk-based indexes making it a defensible default [[2]](#ref-2).

## Operations

- **Qdrant**: Offers payload filtering capabilities, allowing metadata-based query-time filters without additional queries [[6]](#ref-6).
- **pgvector**: Supports hybrid search, row-level security for multi-tenant RAG, and connection pooling through managed platforms like Supabase and Neon [[3]](#ref-3).

## Cost

- **Qdrant Cloud**: Cheapest among managed dedicated DBs at scale (~$65/mo for 10M vectors) with no per-query fee [[5]](#ref-5).
- **pgvector**: Free to use, only requiring a Postgres instance cost ($50–$180/mo on RDS) [[5]](#ref-5).

## Recommendation

| Option | Recommendation |
| --- | --- |
| Qdrant | Optimal for high-scale applications requiring low latency and high throughput. Consider the higher costs but benefits in performance and payload filtering capabilities. |
| pgvector | Suitable for smaller scale deployments or cost-sensitive projects, leveraging Postgres' existing infrastructure with flexible deployment options. |

**Caveats**: The comparison is based on findings from 2026, which may not reflect current market conditions.

## Caveats

- The comparison is based on findings from 2026, which may not reflect current market conditions.

## Sources

1. <a id="ref-1"></a>[Qdrant Performance: Benchmarks, Latency & Limits 2026 | ComparEdge](https://comparedge.com/tools/qdrant/performance)
2. <a id="ref-2"></a>[Vector Database Benchmarks 2026: 4 Ranked by QPS, Recall & Cost](https://iotdigitaltwinplm.com/vector-database-benchmarks-2026-pinecone-weaviate-qdrant-milvus/)
3. <a id="ref-3"></a>[pgvector Architecture Diagram: Postgres as Your Vector ...](https://architecturediagram.ai/blog/pgvector-architecture-diagram)
4. <a id="ref-4"></a>[PostgreSQL pgvector Architecture: Production System Design](https://markaicode.com/architecture/postgresql-ai-architecture/)
5. <a id="ref-5"></a>[Vector Database Pricing 2026: Pinecone, pgvector, Weaviate](https://spendark.com/blog/vector-database-pricing/)
6. <a id="ref-6"></a>[How to Deploy Qdrant in Production (2026 Complete Guide)](https://www.antryk.com/blog/how-to-deploy-qdrant-in-production-the-complete-guide-2026)
