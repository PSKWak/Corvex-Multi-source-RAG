Project: Multi-Source RAG for Technical Support. 
TO DO : You need to build a RAG system that can answer customer questions about a fictional software product by retrieving information from multiple distinct knowledge sources: product documentation, customer forums, and technical blog posts.

Workflow:
1. Create or suggest 3 data sources: 1. Product Document 2. customer forums 3. technical blog posts
2. Create Data Ingestion Pipeline : Use elasticsearch free tier to 
    		   
                   Parse the Data
                          │
                          ▼
             Structure-Aware Semantic Chunking
                          │
                          ▼
                  Metadata Enrichment
                          │
                          ▼
                 Generate Embeddings
                          │
                          ▼
        Create a Vector database using weaviate
                          │
                          ▼
              ┌─────────────────────────┐
              │    Hybrid Retrieval     │
              │                         │
              │ Vector Search + BM25    │
              └────────────┬────────────┘
                           ▼
          Retrieve candidates from:
            ├── Documentation
            ├── Customer Forums
            └── Technical Blogs
                     ↓
          Source Weighting  (1. Product Document 2. customer forums 3. technical blog posts)
      
                           │
                           ▼
         Reranker (Use Cohere Rerank free tier)
                           │
                           ▼
                    Top-K Evidence
                           │
                           ▼
                Contradiction Detection
                           │
                           ▼
                 Conflict Resolution
                           │
                           ▼
                    Context Builder
                           │
                           ▼
                     LLM Generation
                           │
                           ▼
                   Draft Answer
                           │
                           ▼
               ┌─────────────────────┐
               │   SELF-CHECK        │
               │                     │
               │ Grounding           │
               │ Citation            │
               │ Relevance           │
               │ Version             │
               │ Contradiction       │
               └──────────┬──────────┘
                          │
                    ┌─────┴─────┐
                    ▼           ▼
                  PASS         FAIL
                    │           │
                    │           ▼
                    │      Corrective Action
                    │           │
                    │      Re-retrieve /
                    │       Re-rank /
                    │       Regenerate
                    │           │
                    │           ▼
                    │       Self-Check
                    │
                    ▼
              Answer + Citations
                    │
                    ▼
          Provenance + Logging
                    │
                    ▼
             User Feedback
                    │
                    ▼
             Feedback Store
                    │
                    ▼
                RAGAS 
                    
TOOLS TO USE:
1. elasticsearch free tier
2. Weaviate free tier
3. Cohere Rerank free tier

Requirements:
1. Create or source three different types of data (documentation, forums, blogs)
2. Implement a chunking strategy appropriate for each data source
3. Build a retrieval system that can intelligently weigh and combine results from all sources
4. Implement a reranking mechanism to improve relevance
5. Design a mechanism to handle contradictions between sources
6. Include logging to track which sources are being used for each response

          
Retriever Structure for reference:
Retriever
   │
   ├── Cohere Rerank
   │
   └── BGE Reranker
          ↓
       Compare
Then measure:
NDCG@K
MRR
Context Precision
Context Recall
Answer Correctness
Latency

Evaluation Dataset:
evaluation/
├── questions.json
├── ground_truth.json
├── retrieval_cases.json
└── contradiction_cases.json

Include questions specifically designed for:

documentation-only answers
forum-only answers
blog-only answers
cross-source answers
contradictory sources
version conflicts
outdated information
exact error codes
no-answer questions
prompt injection