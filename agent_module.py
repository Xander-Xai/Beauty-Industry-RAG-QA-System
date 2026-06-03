import json
import sys
import torch
from rag_module import RAGModule
from knowledge_graph import KnowledgeGraph
from multimodal_module import MultimodalModule
from transformers import AutoTokenizer, AutoModelForCausalLM, MT5Tokenizer, MT5ForConditionalGeneration
import faiss
import numpy as np

# 加载配置（处理转义引号问题）
with open('config.json', encoding='utf-8', errors='replace') as config_file:
    content = config_file.read().replace('\\"', '"')
config = json.loads(content)


class Agent:
    def __init__(self):
        print('开始初始化各模块')
        self.dialog_history = []        # 对话历史
        self.max_dialog_length = 5      # 滑动窗口长度
        self.max_summary_length = 400   # 摘要长度限制

        # 意图分类器和实体提取器（规则实现，无需预训练模型）
        self._intent_classifier = None
        self._entity_extractor = None

        # Elasticsearch 连接（可选，失败则跳过）
        self.elasticsearch = None
        try:
            from elasticsearch import Elasticsearch
            self.elasticsearch = Elasticsearch([config.get('elasticsearch_host', 'localhost:9200')])
        except Exception as e:
            print(f'Elasticsearch 连接失败，跳过: {e}')

        self.rag_module = RAGModule()   # RAG模块
        self.knowledge_graph = KnowledgeGraph()         # 知识图谱
        self.multimodal_module = MultimodalModule()     # 多模态模块
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # ChatGLM3-6B（可选，失败则降级）
        self.chatglm_model = None
        self.chatglm_tokenizer = None
        try:
            llm_path = config.get('llm_path', './chatglm3-6b')
            self.chatglm_tokenizer = AutoTokenizer.from_pretrained(llm_path, trust_remote_code=True)
            self.chatglm_model = AutoModelForCausalLM.from_pretrained(llm_path, trust_remote_code=True).to(self.device)
        except Exception as e:
            print(f'ChatGLM3-6B 加载失败: {e}')

        # FAISS 索引（可选）
        self.index = None
        self.vector_data = None
        try:
            if config.get('vector_dbindex_path'):
                self.index = faiss.read_index(config['vector_dbindex_path'])
                self.dimension = self.index.d
        except Exception as e:
            print(f'FAISS 索引加载失败: {e}')

        try:
            if config.get('vector_vectors_path'):
                self.vector_data = np.load(config['vector_vectors_path'])
        except Exception as e:
            print(f'向量数据加载失败: {e}')

        # MT5 摘要模型（可选）
        self.mt5_model = None
        self.mt5_tokenizer = None
        try:
            summary_path = config.get('summary_model_path', './ms-marco-TinyBERT-L-2-v2')
            self.mt5_tokenizer = MT5Tokenizer.from_pretrained(summary_path)
            self.mt5_model = MT5ForConditionalGeneration.from_pretrained(summary_path).to(self.device)
        except Exception as e:
            print(f'MT5 摘要模型加载失败: {e}')

        # 预训练的意图/NER模型（可选）
        self.intent_model = None
        self.intent_tokenizer = None
        self.ner_model = None
        self.ner_tokenizer = None
        self._try_load_pretrained_models()

        print('各模块初始化完成')

    def _try_load_pretrained_models(self):
        """尝试加载预训练的意图识别和NER模型"""
        try:
            intent_path = config.get('intent_model_path')
            if intent_path:
                self.intent_tokenizer = AutoTokenizer.from_pretrained(intent_path)
                self.intent_model = AutoModelForCausalLM.from_pretrained(intent_path).to(self.device)
                print(f'意图识别模型加载完成: {intent_path}')
        except Exception as e:
            print(f'意图识别模型加载失败，使用规则兜底: {e}')

        try:
            ner_path = config.get('ner_model_path')
            if ner_path:
                from transformers import AutoModelForTokenClassification
                self.ner_tokenizer = AutoTokenizer.from_pretrained(ner_path)
                self.ner_model = AutoModelForTokenClassification.from_pretrained(ner_path).to(self.device)
                print(f'NER 模型加载完成: {ner_path}')
        except Exception as e:
            print(f'NER 模型加载失败，使用规则兜底: {e}')

    @property
    def intent_classifier(self):
        if self._intent_classifier is None:
            from agent_module.intent_ner import IntentClassifier
            self._intent_classifier = IntentClassifier()
        return self._intent_classifier

    @property
    def entity_extractor(self):
        if self._entity_extractor is None:
            from agent_module.intent_ner import EntityExtractor
            self._entity_extractor = EntityExtractor()
        return self._entity_extractor

    def summarize_dialog_history(self, dialog_history):
        # 将对话历史转换为文本
        dialog_history_text = "\n".join([
            f"用户: {entry.get('user_input', '')}\n系统: {entry.get('response', '')}"
            for entry in self.dialog_history
        ])

        if not self.mt5_model or not self.mt5_tokenizer:
            # 降级：返回最近一轮对话
            if self.dialog_history:
                last = self.dialog_history[-1]
                return f"用户问了关于「{last.get('user_input', '')[:50]}」的问题"
            return "（无历史对话）"

        # 分词并生成摘要
        inputs = self.mt5_tokenizer(
            "summarize: " + dialog_history_text,
            return_tensors='pt', truncation=True, max_length=512
        )
        input_ids = inputs['input_ids'].to(self.device)
        attention_mask = inputs['attention_mask'].to(self.device) if 'attention_mask' in inputs else None

        with torch.no_grad():
            outputs = self.mt5_model.generate(
                input_ids=input_ids,
                attention_mask=attention_mask,
                max_length=self.max_summary_length,
                length_penalty=1.0,
                num_beams=4,
                early_stopping=True
            )
        summary = self.mt5_tokenizer.decode(outputs[0], skip_special_tokens=True)
        return summary

    def predict_intent(self, text: str) -> int:
        """
        预测用户意图

        优先级：
        1. 预训练 BERT 模型（如果可用）
        2. 规则分类器（基于关键词）
        """
        # 优先使用预训练模型
        if self.intent_model and self.intent_tokenizer:
            inputs = self.intent_tokenizer(text, return_tensors='pt').to(self.device)
            with torch.no_grad():
                outputs = self.intent_model(**inputs)
            predicted_class = torch.argmax(outputs.logits, dim=1).item()
            return predicted_class

        # 降级：规则分类器
        return self.intent_classifier.predict(text)

    def extract_entities(self, text: str) -> list:
        """
        提取命名实体

        优先级：
        1. 预训练 NER 模型（如果可用）
        2. 规则提取器（基于正则）
        """
        # 优先使用预训练模型
        if self.ner_model and self.ner_tokenizer:
            inputs = self.ner_tokenizer(text, return_tensors='pt').to(self.device)
            with torch.no_grad():
                outputs = self.ner_model(**inputs)
            predictions = torch.argmax(outputs.logits, dim=2)
            tokens = self.ner_tokenizer.convert_ids_to_tokens(inputs['input_ids'][0])
            entities = []
            for token, prediction in zip(tokens, predictions[0].cpu().numpy()):
                if prediction != 0:  # 0 通常表示非实体
                    entities.append(token)
            return entities

        # 降级：规则提取
        result = self.entity_extractor.extract(text)
        return result["all"]

    def handle_query(self, user_input: str, image_path: str = None) -> str:
        """
        处理用户查询

        意图路由逻辑：
        - 0 (汽车/产品): RAG 检索 + ES 实体精确匹配
        - 1 (法规/合规): ES 精确匹配
        - 2 (价格/规格): RAG + 简短回答
        - 3 (图片/视觉): 启用多模态
        - 4 (对比分析): 多文档综合
        - 5 (闲聊): 直接回答
        """
        # 滑动窗口管理
        if len(self.dialog_history) >= self.max_dialog_length:
            self.dialog_history.pop(0)

        # 图片处理
        if image_path:
            image_description = self.multimodal_module.process_image(image_path)
            user_input = f"{user_input} {image_description}"

        self.dialog_history.append({'user_input': user_input})

        # 意图识别
        intent = self.predict_intent(user_input)
        print(f'用户意图: {intent} ({self.intent_classifier.get_intent_name(intent)})')

        context = ""

        # 根据意图选择检索策略
        if intent == 0:  # 汽车/产品相关
            entities = self.extract_entities(user_input)
            search_query = " ".join(entities) if entities else user_input
            context = self._search_with_fallback(search_query)

        elif intent == 1:  # 法规/合规
            entities = self.extract_entities(user_input)
            search_query = " ".join(entities) if entities else user_input
            context = self._search_es(search_query) or self._search_with_fallback(search_query)

        elif intent == 3:  # 图片/视觉
            context = self._search_with_fallback(user_input)
            # 多模态图片描述已在前面拼接到 user_input

        else:  # 其他意图
            context = self._search_with_fallback(user_input)

        # 知识图谱补充
        enriched_context = {}
        try:
            enriched_context = self.knowledge_graph.enrich(context)
        except Exception as e:
            print(f'知识图谱补充失败: {e}')

        # 历史对话摘要
        dialog_summary = self.summarize_dialog_history(self.dialog_history)

        # 构造 Prompt
        enriched_text = self._format_enriched_context(enriched_context)
        prompt = (
            f"这是之前对话的总结:\n{dialog_summary}\n\n"
            f"相关补充信息: {enriched_text}\n\n"
            f"对于这个问题: {user_input}\n\n"
            f"有以下补充信息以供参考: {context}\n\n"
            "请你给出问题的回复："
        )

        # LLM 生成
        if self.chatglm_model and self.chatglm_tokenizer:
            inputs = self.chatglm_tokenizer(prompt, return_tensors='pt')
            input_ids = inputs['input_ids'].to(self.device)
            attention_mask = inputs['attention_mask'].to(self.device) if 'attention_mask' in inputs else None
            outputs = self.chatglm_model.generate(
                input_ids=input_ids,
                attention_mask=attention_mask,
                max_length=512,
                num_beams=5,
                no_repeat_ngram_size=2,
                early_stopping=True
            )
            response_text = self.chatglm_tokenizer.decode(outputs[0], skip_special_tokens=True)
        else:
            # 降级：返回上下文摘要
            response_text = f"根据相关资料，关于「{user_input}」的信息如下：{context[:200]}"

        self.dialog_history.append({'response': response_text})
        return response_text

    def _search_with_fallback(self, query: str) -> str:
        """检索（优先 RAG，降级为直接搜索）"""
        try:
            rag_results = self.rag_module.query(query)
            if rag_results and rag_results[0]:
                return " ".join(rag_results[0])
        except Exception as e:
            print(f'RAG 检索失败: {e}')

        # RAG 不可用时尝试 ES
        return self._search_es(query)

    def _search_es(self, query: str) -> str:
        """Elasticsearch 搜索"""
        if not self.elasticsearch:
            return ""

        try:
            es_index = config.get('elasticsearch_index', 'rag_content')
            result = self.elasticsearch.search(
                index=es_index,
                body={"query": {"match": {"content": query}}}
            )
            hits = result.get('hits', {}).get('hits', [])
            texts = [hit['_source'].get('content', '') for hit in hits]
            return " ".join(texts)
        except Exception as e:
            print(f'ES 搜索失败: {e}')
            return ""

    def _format_enriched_context(self, enriched_context: dict) -> str:
        """格式化知识图谱补充结果"""
        if not enriched_context:
            return "（无）"

        parts = []
        for keyword, results in enriched_context.items():
            if results:
                parts.append(f"{keyword}: {'; '.join(str(r) for r in results)}")
        return " | ".join(parts) if parts else "（无）"

    def get_dialog_history(self):
        return self.dialog_history


if __name__ == "__main__":
    agent = Agent()
    while True:
        try:
            sys.stdout.write("请输入问题：")
            sys.stdout.flush()
            user_input = sys.stdin.readline().strip()
            if user_input.lower() == 'exit':
                break

            response = agent.handle_query(user_input)
            print("系统：", response)
        except UnicodeDecodeError as e:
            print(f"输入处理错误: {e}")
