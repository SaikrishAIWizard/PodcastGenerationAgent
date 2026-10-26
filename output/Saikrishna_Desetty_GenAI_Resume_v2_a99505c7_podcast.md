HOST: Welcome to the AI Engineering Podcast. Today I'm joined by Desetty Vijaya Satya Gowri Saikrishna, a Generative AI Engineer based in Hyderabad. Thanks for being here.
EXPERT: Thanks for having me.
HOST: Let's start with your background. I see you have an M.Tech in Industrial AI from IIT Madras. How did that program shape your career in generative AI?
EXPERT: The M.Tech gave me a solid foundation in applying AI to real-world industrial problems. It combined theory with hands‑on projects, which prepared me to build large language model applications, especially in enterprise settings.
HOST: Speaking of large language models, your professional summary mentions building LLM‑powered chat applications, retrieval‑augmented generation pipelines, and orchestrator‑based backend services on Azure and GCP. Can you break down what those components involve?
EXPERT: Sure. The chat applications are built on top of LLMs to understand user queries and generate responses. Retrieval‑augmented generation, or RAG, adds a knowledge base so the model can pull factual information before answering. Orchestrator‑based backends coordinate multiple services—like prompt generation, data retrieval, and response formatting—using cloud platforms such as Azure and Google Cloud.
HOST: You also highlight strong Python and FastAPI skills. How do those fit into your AI pipelines?
EXPERT: Python is the lingua franca for AI, and FastAPI lets me expose model inference and data processing as lightweight, high‑performance HTTP endpoints. This makes it easy to integrate with other services, handle authentication, and scale on cloud infrastructure.
HOST: Your resume lists a lot of technical skills. Could you give us a quick overview of the key areas you focus on?
EXPERT: Absolutely. In generative AI, I work with LLMs, prompt engineering, RAG, context and memory management, and tools like DSPy for prompt optimization. For agentic AI, I use orchestration frameworks such as LangGraph and CrewAI, and models from Anthropic. On the backend side, I develop APIs with FastAPI, Flask, or Django, and secure them with OAuth 2.0 and OIDC. Cloud-wise, I’m experienced with Azure services like Cosmos DB and Managed Identity, GCP services like Cloud Run and Speech‑to‑Text, and AWS SageMaker. Finally, I handle data with SQL, NoSQL, PySpark, and monitor everything with observability tools.
HOST: That's a broad stack. Let’s dive into one of your projects—the financial data query assistant for an enterprise client. What was the core problem you solved?
EXPERT: The client needed a conversational interface that could answer financial and general queries using their internal data. I built a FastAPI orchestration agent that extracts entities from user input, validates them, and asks follow‑up questions when information is missing.
HOST: How did you connect the language model to the underlying data?
EXPERT: I integrated Databricks Genie Space as the LLM query engine. The system takes the refined user prompt, translates it into SQL, runs the query, and then summarizes the results back to the user.
HOST: You mentioned chat history and long‑term memory. How did you implement that?
EXPERT: I stored conversation context in Azure Cosmos DB for NoSQL storage and used Blob Storage for larger artifacts. This lets the assistant maintain multi‑turn context and retrieve past interactions when needed.
HOST: Evaluation is critical. How did you assess the quality of the generated SQL and answers?
EXPERT: I built an LLM‑as‑a‑Judge framework that measures error rate in SQL generation, faithfulness to the source data, and relevance of the answer. I also applied DSPy prompt optimization techniques—specifically GEPA and MIPROv2—to improve prompt quality, and added an observability layer to monitor the whole pipeline.
HOST: Moving to another project, the incident support bot for an enterprise client. What were its main capabilities?
EXPERT: The bot runs on FastAPI and integrates with Microsoft Teams via Adaptive Cards. It automates incident troubleshooting by retrieving relevant knowledge from a RAG‑enhanced knowledge base, generating step‑by‑step workarounds, and either resolving the issue automatically or escalating to a Level‑3 team.
HOST: How did you keep the knowledge base up to date?
EXPERT: I built a document‑update tracking service that monitors changes in source documents and refreshes the RAG index accordingly. I also added key‑value caching to speed up retrieval and generation.
HOST: Security is a big concern in enterprise bots. What measures did you implement?
EXPERT: I designed a secretless Azure architecture using Managed Identity and Entra ID for single sign‑on, combined with OAuth 2.0/OIDC for token handling. The Bot Framework webhook validation ensures only authorized calls, and I enforced PII handling controls throughout.
HOST: You also tracked operational metrics. Which ones were most important?
EXPERT: First‑call resolution (FCR), mean time to resolution (MTTR), RAG faithfulness, and context relevance. All of these were captured in the observability layer for continuous improvement.
HOST: Your third highlighted project involves a CRM sales conversation validation workflow for an energy‑sector client in Australia. What problem were you addressing there?
EXPERT: The client needed to verify that sales calls adhered to a set of 50 scripted requirements—checking disclosures, consent, and sales criteria. The system extracts required details from the conversation, measures entity‑extraction precision and recall, and flags any missing or non‑compliant elements.
HOST: How did you handle dropped calls or incomplete data?
EXPERT: I wrote Python logic that preserves whatever data was already collected, detects missing fields, and then uses an LLM fallback to interpret ambiguous answers. If the LLM can’t resolve the ambiguity, the system triggers an automated voice‑agent follow‑up call to gather the missing information.
HOST: Which cloud services powered that workflow?
EXPERT: The solution runs on Google Cloud Platform—using Cloud Run for the API, Cloud Scheduler for periodic tasks, and Cloud Run Jobs for the voice‑agent. Speech‑to‑Text and Text‑to‑Speech services handle the audio conversion.
HOST: And you monitored performance there as well?
EXPERT: Yes, we tracked call success rate, LLM fallback rate, latency, and word error rate (WER). An observability layer gave us real‑time insights into those metrics.
HOST: Let’s talk about your education. You earned a B.Tech in Electronics & Communication before your M.Tech. How did that engineering background contribute to your AI work?
EXPERT: The electronics and communication fundamentals gave me a strong grasp of signal processing and systems thinking, which translate well to designing data pipelines and understanding the hardware constraints of AI deployments.
HOST: You hold several certifications—GenAI Assisted Engineer from Virtusa, AWS Machine Learning Specialty, and UiPath Advanced RPA Developer. How have those certifications influenced your projects?
EXPERT: The GenAI certification deepened my expertise in prompt engineering and LLM evaluation. The AWS Machine Learning Specialty validated my ability to design and deploy ML models on AWS, which complements my work on Azure and GCP. The UiPath RPA certification helped me automate repetitive tasks and integrate robotic process automation with AI workflows.
HOST: Your achievements include being recognized as a top performer at Virtusa, clearing Phase 1 of the American Idol Hackathon with an NLP‑based MedCode solution, and being a finalist in NeuralHack Season 3. Can you share a bit about those experiences?
EXPERT: Being named HIL FY24 Top Performer reflected consistent delivery of high‑impact AI solutions. The MedCode hack involved building an NLP system to code medical records, which advanced to Phase 1 of the American Idol Hackathon in 2022. In NeuralHack Season 3, I was a finalist, showcasing my ability to tackle data‑science challenges under time pressure.
HOST: That’s an impressive portfolio. To wrap up, could you give our listeners a quick recap of the core themes that define your work?
EXPERT: Certainly. My focus is on building end‑to‑end generative AI solutions that combine LLMs with robust retrieval, secure cloud backends, and observability. I emphasize prompt optimization, evaluation frameworks like LLM‑as‑a‑Judge, and seamless integration with enterprise data and security standards. Across finance, incident support, and sales validation, the goal is to deliver reliable, context‑aware AI assistants that improve efficiency and compliance.
HOST: Thanks, Vijaya, for sharing the depth and breadth of your experience. It’s been a pleasure.
EXPERT: Thank you. Glad to be here.
