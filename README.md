# 📰 TruthLens – AI Fake News Detection System

TruthLens is an AI-powered fake news detection system that verifies the authenticity of news and claims using a hybrid approach. It combines a local Large Language Model (LLM) with real-time web search to provide accurate, explainable, and multilingual fact-checking.

---

## 🚀 Features

* 🔍 **Real-time Fact Checking** using Tavily web search
* 🤖 **LLM-Based Analysis** using LLaMA 3.1 via Ollama
* 🌐 **Multilingual Support** (English, Tamil, Telugu, Hindi)
* 🗣️ **Voice Input Support** (speech-to-text)
* 🔐 **User Authentication System** (Login/Register/Google Auth)
* 📜 **History Tracking** of analyzed claims
* 🧠 **Explainable AI Output** (Verdict + Explanation + Correction)

---

## 🧠 How It Works

1. User enters a claim / article / voice input
2. System detects input type and language
3. Real-time evidence is fetched using Tavily API
4. LLM (LLaMA 3.1 via Ollama) analyzes the content
5. System generates:

   * ✅ Verdict (TRUE / FALSE / UNCERTAIN)
   * 📖 Explanation
   * 🔄 Corrected Statement

---

## 🛠️ Tech Stack

* **Backend:** Flask (Python)
* **Database:** MySQL
* **AI Model:** LLaMA 3.1 (via Ollama)
* **Search API:** Tavily (RAG-based retrieval)
* **Frontend:** HTML, CSS, JavaScript
* **Authentication:** Flask Session + Google OAuth

---

## 📂 Project Structure

```
FAKE_NEWS_DETECTION/
│── app.py
│── requirements.txt
│── .gitignore
│── extension/
│── templates/
```

---

## ⚙️ Setup Instructions

### 1. Clone the repository

```
git clone https://github.com/vishnu23-sp/Fake_News_Detection.git
cd Fake_News_Detection
```

### 2. Install dependencies

```
pip install -r requirements.txt
```

### 3. Create `.env` file

```
SECRET_KEY=your_secret_key
MAIL_USERNAME=your_email
MAIL_PASSWORD=your_password
DB_HOST=localhost
DB_USER=root
DB_PASS=your_db_password
TAVILY_API_KEY=your_api_key
GOOGLE_CLIENT_ID=your_google_client_id
```

### 4. Run Ollama (for LLaMA model)

Make sure Ollama is running locally:

```
ollama run llama3.1:8b
```

### 5. Run the application

```
python app.py
```


## 🎯 Future Improvements

* 🌐 Deploy as a web application
* 📊 Improve model accuracy with fine-tuning
* 📱 Mobile app integration
* 🔎 Advanced misinformation detection

---

## 📌 Conclusion

TruthLens provides a scalable and intelligent solution to detect fake news using modern AI techniques like LLMs and Retrieval-Augmented Generation (RAG). It enhances transparency by providing evidence-based explanations, making it useful for real-world applications.

---

## 👨‍💻 Author

**Vishnu S**
AI/ML Student | Aspiring Machine Learning Engineer

---
