"""Locust 压测脚本 — 化妆品 RAG 系统。"""
import random
from locust import HttpUser, task, between

QUERIES_REGULATION = [
    "化妆品中铅含量的限量标准是什么？",
    "防晒产品需要哪些法规认证？",
    "化妆品标签需要标注哪些成分信息？",
    "儿童化妆品有什么特殊规定？",
    "进口化妆品的备案流程是什么？",
]

QUERIES_INGREDIENT = [
    "烟酰胺的安全浓度是多少？",
    "透明质酸钠有什么功效？",
    "水杨酸在化妆品中的使用限制？",
    "视黄醇的刺激性如何降低？",
    "熊果苷和曲酸哪个美白效果更好？",
]

QUERIES_GENERAL = [
    "保湿面霜的常见成分有哪些？",
    "如何判断化妆品是否过期？",
    "敏感肌肤适合用什么类型的护肤品？",
    "防晒霜的SPF和PA值是什么意思？",
    "化妆品中的防腐剂有哪些？",
]


class RAGUser(HttpUser):
    wait_time = between(2, 5)

    def on_start(self):
        self.user_id = f"loadtest_user_{random.randint(1000, 9999)}"
        self.headers = {
            "Content-Type": "application/json",
            "X-User-ID": self.user_id,
            "X-Role-Mask": str(random.choice([0x02, 0x04, 0x08, 0x10])),
            "X-Dept-Mask": str(random.choice([0x01, 0x02, 0x04])),
        }
        self.session_id = None

    @task(40)
    def single_query(self):
        query = random.choice(QUERIES_REGULATION + QUERIES_INGREDIENT + QUERIES_GENERAL)
        self.client.post("/api/query", json={"query": query}, headers=self.headers, name="/api/query")

    @task(30)
    def multi_turn_chat(self):
        if self.session_id is None:
            query = random.choice(QUERIES_REGULATION)
            self.session_id = f"session_{self.user_id}_{random.randint(1000, 9999)}"
        else:
            query = random.choice(["能详细说一下吗？", "这个法规适用于哪些产品？", "有没有具体的检测方法？"])
        self.client.post("/api/chat", json={"query": query, "session_id": self.session_id}, headers=self.headers, name="/api/chat")

    @task(10)
    def health_check(self):
        self.client.get("/api/health", name="/api/health")

    @task(20)
    def regulation_query(self):
        query = random.choice(QUERIES_REGULATION)
        self.client.post("/api/query", json={"query": query}, headers=self.headers, name="/api/query [regulation]")
