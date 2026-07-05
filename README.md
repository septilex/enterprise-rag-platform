🚀 Enterprise RAG Platform (Knowledge Assistant)

A production-grade AI system that allows users to upload documents and ask intelligent questions grounded strictly in their data with citations.

📌 What This Project Does
Upload PDFs and text files
AI automatically indexes and understands documents
Ask natural language questions over your data
Get grounded, citation-backed answers
Multi-user + multi-collection support
Admin dashboard for system monitoring
Real-time ingestion pipeline with worker system
⚙️ Key Features
📄 Document Intelligence
PDF + TXT ingestion
Smart chunking + embeddings
Retrieval-based QA system
💬 AI Chat System
Ask questions over uploaded documents
Grounded responses only (no hallucinations)
Citation-backed answers with sources
🧠 Enterprise Control Panel
System health monitoring
Ingestion tracking (runs, success, failures)
Worker status monitoring
Retry failed jobs
🔐 Security & Access
Role-based access control (Admin / Editor / Viewer)
Multi-tenant architecture
Audit logging system
🚀 How to Run the Project
Backend
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
Frontend
cd frontend
npm install
npm run dev
🌐 Open Application
http://localhost:5173
🧪 How to Use (Demo Flow)
Open the app in browser
Select "demo" collection
Upload a PDF or text file
Wait for indexing to complete
Ask questions like:
What is my CGPA?
What projects do I have?
What is this document about?
🔥 System Highlights
Real-time ingestion pipeline
Worker-based background processing
Admin monitoring dashboard
Fault recovery system (retry + DLQ)
Production-grade architecture design
🧰 Tech Stack
FastAPI (Python backend)
React + TypeScript frontend
Redis queue system
Vector-based retrieval system
RBAC + audit logging
📊 Project Status

Fully working enterprise-grade RAG system prototype
with ingestion, retrieval, RBAC, and operations dashboard.
