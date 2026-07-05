# 🚀 Enterprise RAG Platform (Knowledge Assistant)

A production-grade AI system that allows users to upload documents and ask intelligent questions grounded strictly in their data with citations.

## 📌 Project Overview

This project provides a complete end-to-end RAG (Retrieval-Augmented Generation) solution:
- **Upload PDFs and text files**
- **AI indexes and understands documents**
- **Ask natural language questions over your data**
- **Get grounded answers with citations**
- **Multi-user & multi-collection support**
- **Admin dashboard for system monitoring**
- **Real-time ingestion pipeline with worker system**

## ⚙️ Key Features

### 📄 Document Intelligence
- PDF & TXT ingestion
- Smart chunking & embeddings
- Retrieval-based QA system

### 💬 AI Chat System
- Ask questions over uploaded documents
- Grounded responses only (no hallucinations)
- Citation-backed answers with sources

### 🧠 Enterprise Control Panel
- System health monitoring
- Ingestion tracking (runs, success, failures)
- Worker status monitoring
- Retry failed jobs

### 🔐 Security & Access
- Role-based access control (Admin / Editor / Viewer)
- Multi-tenant architecture
- Audit logging system

## 🧰 Tech Stack
- **Backend:** FastAPI (Python)
- **Frontend:** React + TypeScript
- **Queues:** Redis
- **Retrieval:** Vector-based system
- **Security:** RBAC & audit logging

## 🚀 Setup & Local Development (5-Minute Quickstart)

Follow these steps to run the platform locally.

### 1. Start the Backend
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### 2. Start the Frontend
```bash
cd frontend
npm install
npm run dev
```

### 3. Open in Browser
Navigate to [http://localhost:5173](http://localhost:5173)

## 🧪 Demo Flow (How to Test)

1. Open the app in your browser (`http://localhost:5173`).
2. Select the **'demo'** collection.
3. **Upload** a PDF or text file.
4. Wait for indexing to complete.
5. **Ask questions** like:
   - *"What is my CGPA?"*
   - *"What projects do I have?"*
   - *"What is this document about?"*

## 📊 Project Status

**Fully working enterprise-grade RAG system prototype** featuring real-time ingestion, worker-based background processing, vector retrieval, role-based access control (RBAC), fault recovery (retry + DLQ), and an operations dashboard.
