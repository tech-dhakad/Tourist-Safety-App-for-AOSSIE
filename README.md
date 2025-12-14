---

🌍 Raahi — Hybrid Decentralized Tourist Safety & Navigation Platform

📌 Overview

Raahi is a hybrid-decentralized tourist safety and navigation web application designed to improve traveler security, situational awareness, and reliability in unfamiliar regions. The platform strategically combines decentralized storage, open routing systems, peer-assisted real-time communication, and AI-powered guidance, while maintaining performance and usability through selective centralized components.

Instead of relying entirely on a single centralized backend, Raahi distributes trust-critical responsibilities across open and decentralized technologies, reducing single-point failures and vendor lock-in.


---

🚀 Key Features

🧭 Open-source route navigation using OSRM & OpenStreetMap

🛡 Tourist safety reporting and incident tracking

📡 Real-time location & alert sharing (Io-based communication)

🧠 AI-assisted safety guidance (optional & user-controlled)

🔗 Decentralized data storage using IPFS

🔍 Transparent & auditable data flow



---

🧩 Hybrid Decentralized Architecture

Raahi follows a practical hybrid decentralization model, where decentralization is applied where trust, transparency, and resilience matter most, while centralized components are used only for performance-critical tasks.

🔹 Decentralized Components

IPFS (InterPlanetary File System)
Safety reports, incident metadata, and route-related records are stored using content-addressed identifiers (CIDs), ensuring tamper-resistance and persistence beyond a single server.

OpenStreetMap + OSRM
Routing and navigation rely on community-maintained open datasets instead of proprietary routing engines, enabling auditability and infrastructure independence.

User-Controlled AI Access
AI features are powered via Google Gemini, where users can provide their own API keys, removing forced dependency on a centralized AI provider. Rule-based fallbacks ensure functionality even without AI access.



---

🔹 Semi-Centralized / Coordinating Components

Flask Backend
Acts as a lightweight coordination and validation layer rather than a data owner. The backend is designed to be stateless, enabling future federation or multi-node deployment.

Mapping UI Layer
Google Maps is used at the visualization layer for familiarity and performance, while the core logic remains provider-agnostic and replaceable.



---

🛠 Tech Stack

Backend: Flask (Python)

Frontend: HTML, CSS, JavaScript

Routing: OSRM + OpenStreetMap

Decentralized Storage: IPFS

AI Integration: Google Gemini API (optional, user-provided)

Realtime Communication: Socket-based Io model

Maps Visualization: Google Maps (UI layer)



---

⚙ Installation & Setup

# Clone the repository
git clone https://github.com/tech-dhakad/Tourist-Safety-App-for-AOSSIE.git
cd Tourist-Safety-App-for-AOSSIE

# Install dependencies
pip install -r requirements.txt

# Run the application
python app.py

> ⚠ Note: AI features require a user-provided Gemini API key. Core functionality works without AI.




---

🔮 Future Implementation Roadmap

Raahi is designed to evolve towards deeper decentralization:

Multi-node IPFS pinning for redundancy

Federated backend architecture

Decentralized Identity (DID) based authentication

On-chain verification of safety reports

DAO-based community moderation

Fully modular multi-LLM support


(See future roadmap contributions via Pull Requests.)


---

🤝 Open-Source & Contributions

This project is developed as part of an open-source initiative aligned with AOSSIE’s decentralization goals.
PR-based workflow will be adopted as the project opens for external community contributions.


e short version nikaal doon 💪
