Enterprise RAG Platform (Knowledge Assistant)

A production-grade AI system that lets users upload documents and ask intelligent questions grounded only in their data.

------------------------------------------------------------
WHAT THIS PROJECT DOES
------------------------------------------------------------
- Upload PDFs and text files
- AI indexes and understands documents
- Ask questions in natural language
- Get grounded answers with citations
- Multi-user + multi-collection support
- Admin dashboard for system monitoring
- Real-time ingestion pipeline with worker system

------------------------------------------------------------
KEY FEATURES
------------------------------------------------------------

Document Intelligence:
- PDF + TXT ingestion
- Chunking + embeddings
- Retrieval-based QA system

AI Chat System:
- Ask questions over documents
- Grounded responses only (no hallucinations)
- Shows sources used for answers

Enterprise Control Panel:
- System health monitoring
- Ingestion tracking (runs, success, failures)
- Worker status monitoring
- Retry failed jobs

Security & Access:
- Role-based access control (Admin / Editor / Viewer)
- Multi-tenant architecture
- Audit logging system

------------------------------------------------------------
HOW TO RUN THE PROJECT
------------------------------------------------------------

Backend:
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000

Frontend:
cd frontend
npm install
npm run dev

Open:
http://localhost:5173

------------------------------------------------------------
HOW TO USE (DEMO FLOW)
------------------------------------------------------------
1. Open the app in browser
2. Select 'demo' collection
3. Upload a PDF or text file
4. Wait for indexing to complete
5. Ask questions like:
   - What is my CGPA?
   - What projects do I have?
   - What is this document about?

------------------------------------------------------------
SYSTEM HIGHLIGHTS
------------------------------------------------------------
- Real-time ingestion pipeline
- Worker-based background processing
- Admin monitoring dashboard
- Fault recovery system (retries + DLQ)
- Production-style architecture

------------------------------------------------------------
TECH STACK
------------------------------------------------------------
- FastAPI (Python backend)
- React + TypeScript frontend
- Redis queue system
- Vector-based retrieval system
- RBAC + audit logging

------------------------------------------------------------
PROJECT STATUS
------------------------------------------------------------
Fully working enterprise-grade RAG system prototype
with ingestion, retrieval, and operations dashboard.
