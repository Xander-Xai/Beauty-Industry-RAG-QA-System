import fitz
import os
import json
from transformers import AutoTokenizer, AutoModel
import torch
import faiss
import numpy as np
from PIL import Image
from transformers import CLIPProcessor, CLIPModel
import pandas as pd
from docx import Document

# 加载配置文件
with open('config.json', encoding='utf-8') as config_file:
    config = json.load(config_file)


# 读取pdf文档内容
def pdf_to_text(pdf_path):
    doc = fitz.open(pdf_path)       # 打开PDF文件
    text = ""
    for page in doc:
        text += page.get_text()     # 从每一页提取文本并累加到text字符串中
    return text

def excel_to_text(excel_path):
    df = pd.read_excel(excel_path)
    text = df.to_string(index=False)
    return text

def word_to_text(word_path):
    doc = Document(word_path)
    text = ""
    for paragraph in doc.paragraphs:
        text += paragraph.text + "\n"
    return text

# 处理图像，使用CLIP模型生成图像的文本描述和嵌入向量
def process_image_with_clip(image_path, clip_model_path):
    processor = CLIPProcessor.from_pretrained(clip_model_path)
    model = CLIPModel.from_pretrained(clip_model_path)

    image = Image.open(image_path).convert("RGB")
    # For image captioning, you might need a separate model or integrate it with CLIP's capabilities
    # For now, let's just get image features from CLIP
    inputs = processor(images=image, return_tensors="pt")
    with torch.no_grad():
        image_features = model.get_image_features(**inputs)
    
    # Placeholder for image description - in a real scenario, you'd use an image captioning model
    image_description = f"这是一个图片，路径为：{os.path.basename(image_path)}"
    
    return image_description, image_features.cpu().numpy()


# 生成知识库向量及索引
def create_vector_index(all_texts, all_vectors, index_path, vector_data_path, text_mapping_path):
    # 保存文本块与索引的映射
    text_mapping = {i: all_texts[i] for i in range(len(all_texts))}
    with open(text_mapping_path, 'w', encoding='utf-8') as f:
        json.dump(text_mapping, f, ensure_ascii=False, indent=4)

    dimension = all_vectors.shape[1]            # 获取向量的维度
    index = faiss.IndexFlatL2(dimension)    # 创建一个FAISS索引，用于L2距离的快速相似性搜索
    index.add(all_vectors)                      # 将向量添加到索引中
    faiss.write_index(index, index_path)    # 将索引写入指定路径的文件中
    np.save(vector_data_path, all_vectors)      # 保存向量到指定路径的文件中


if __name__ == "__main__":
    all_texts = []
    all_vectors = []

    # 处理PDF文件
    if config['rag_data_path'].lower().endswith('.pdf'):
        pdf_text = pdf_to_text(config['rag_data_path'])
        tokenizer = AutoTokenizer.from_pretrained(config['embedding_model_path'])
        model = AutoModel.from_pretrained(config['embedding_model_path'])

        def encode_text(texts):
            inputs = tokenizer(texts, padding=True, truncation=True, return_tensors='pt')
            with torch.no_grad():
                outputs = model(**inputs)
            embeddings = outputs.last_hidden_state.mean(dim=1)
            return embeddings

        text_chunks = [pdf_text[i:i+config['chunk_size']] for i in range(0, len(pdf_text), config['chunk_size'])]
        text_vectors = np.vstack([encode_text(chunk).cpu().numpy() for chunk in text_chunks])
        all_texts.extend(text_chunks)
        all_vectors.extend(text_vectors)

    # 处理Excel文件 (假设Excel路径在config['excel_data_path']中)
    if 'excel_data_path' in config and os.path.exists(config['excel_data_path']):
        excel_text = excel_to_text(config['excel_data_path'])
        tokenizer = AutoTokenizer.from_pretrained(config['embedding_model_path'])
        model = AutoModel.from_pretrained(config['embedding_model_path'])

        def encode_excel_text(texts):
            inputs = tokenizer(texts, padding=True, truncation=True, return_tensors='pt')
            with torch.no_grad():
                outputs = model(**inputs)
            embeddings = outputs.last_hidden_state.mean(dim=1)
            return embeddings

        excel_chunks = [excel_text[i:i+config['chunk_size']] for i in range(0, len(excel_text), config['chunk_size'])]
        excel_vectors = np.vstack([encode_excel_text(chunk).cpu().numpy() for chunk in excel_chunks])
        all_texts.extend(excel_chunks)
        all_vectors.extend(excel_vectors)

    # 处理Word文件 (假设Word路径在config['word_data_path']中)
    if 'word_data_path' in config and os.path.exists(config['word_data_path']):
        word_text = word_to_text(config['word_data_path'])
        tokenizer = AutoTokenizer.from_pretrained(config['embedding_model_path'])
        model = AutoModel.from_pretrained(config['embedding_model_path'])

        def encode_word_text(texts):
            inputs = tokenizer(texts, padding=True, truncation=True, return_tensors='pt')
            with torch.no_grad():
                outputs = model(**inputs)
            embeddings = outputs.last_hidden_state.mean(dim=1)
            return embeddings

        word_chunks = [word_text[i:i+config['chunk_size']] for i in range(0, len(word_text), config['chunk_size'])]
        word_vectors = np.vstack([encode_word_text(chunk).cpu().numpy() for chunk in word_chunks])
        all_texts.extend(word_chunks)
        all_vectors.extend(word_vectors)

    # 处理Text文件 (假设Text路径在config['text_data_path']中)
    if 'text_data_path' in config and os.path.exists(config['text_data_path']):
        with open(config['text_data_path'], 'r', encoding='utf-8') as f:
            text_content = f.read()
        tokenizer = AutoTokenizer.from_pretrained(config['embedding_model_path'])
        model = AutoModel.from_pretrained(config['embedding_model_path'])

        def encode_text_file(texts):
            inputs = tokenizer(texts, padding=True, truncation=True, return_tensors='pt')
            with torch.no_grad():
                outputs = model(**inputs)
            embeddings = outputs.last_hidden_state.mean(dim=1)
            return embeddings

        text_chunks = [text_content[i:i+config['chunk_size']] for i in range(0, len(text_content), config['chunk_size'])]
        text_vectors = np.vstack([encode_text_file(chunk).cpu().numpy() for chunk in text_chunks])
        all_texts.extend(text_chunks)
        all_vectors.extend(text_vectors)

    # 处理图片文件 (假设图片路径在config['image_data_path']中)
    if 'image_data_path' in config and os.path.exists(config['image_data_path']):
        for image_file in os.listdir(config['image_data_path']):
            if image_file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                image_path = os.path.join(config['image_data_path'], image_file)
                image_description, image_features = process_image_with_clip(image_path, config['clip_model_path'])
                all_texts.append(image_description)
                all_vectors.append(image_features.flatten())

    all_vectors = np.array(all_vectors)

    # 创建向量索引并保存
    create_vector_index(all_texts, all_vectors, config['vector_dbindex_path'], config['vector_vectors_path'], config['text_mapping_path'])
