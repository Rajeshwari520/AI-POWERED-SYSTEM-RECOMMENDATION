# NEXUS AI — AI-Powered Product Recommendation System

A full-stack e-commerce web app (Flipkart/Amazon style) with a **hybrid ML recommendation engine**, **AI shopping assistant**, **explainable recommendations**, **price intelligence** and an **admin dashboard**.

## 🛠 Tech Stack
| Part | Tech |
|---|---|
| Frontend | React 19, Vite, JavaScript, CSS |
| Backend | Python, Flask (REST API), SQLite |
| ML | scikit-learn, pandas, NumPy |

```
React (:5173) ──/api──► Flask API (:5000) ──► Hybrid ML engine + SQLite
```

## ✨ Key Features
- **Hybrid recommendations:** content-based + collaborative filtering + popularity
- **Explainable AI:** every recommendation shows *why*
- **Real-time learning:** views, searches, cart and orders update results instantly
- **Cold start** handling for new users
- **AI assistant & smart search:** e.g. "laptop under 70k for AI/ML"
- **Price intelligence:** 30-day price chart with buy/wait verdict
- **Similar products** and **Frequently bought together**
- **Admin dashboard:** revenue, CTR, conversion, top products, one-click model retrain

## 📊 Model Performance (Top-10)
| Model | Precision | Recall | NDCG | Hit Rate |
|---|---|---|---|---|
| Popularity | 0.069 | 0.346 | 0.223 | 0.580 |
| Content-based | 0.141 | 0.704 | 0.537 | 0.914 |
| Collaborative | 0.178 | 0.889 | 0.666 | 0.975 |
| **Hybrid (ours)** | **0.181** | **0.907** | **0.708** | **0.988** |

> Evaluated on 300 synthetic shoppers generated from 15 personas.

## 🚀 Run Locally
**Backend**
```bash
cd backend
python -m venv venv
venv\Scripts\activate        # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
python app.py
```
**Frontend** (new terminal)
```bash
cd frontend
npm install
npm run dev
```
Open **http://localhost:5173**

**Demo login:** `demo@nexus.ai` / `demo123` · Admin: `admin@nexus.ai` / `admin123`

## 📁 Project Structure
```
nexus-ai/
├── backend/
│   ├── app.py              # entry point
│   ├── data/products.csv   # product catalog
│   ├── tests/              # API tests
│   └── nexus/
│       ├── recommender/    # hybrid engine + NLU
│       ├── routes/         # auth, shop, ai, admin APIs
│       └── train.py        # tuning + evaluation
└── frontend/
    └── src/
        ├── components/     # Header, Assistant, Cart...
        └── pages/          # Home, ForYou, Catalog, Admin...
```

## 🔭 Future Scope
Matrix factorisation / neural recommenders · LLM integration · Razorpay payments · Cloud deployment

## 👩‍💻 Author
**Rajeshwari** — [GitHub](https://github.com/Rajeshwari520)
