# -*- coding: utf-8 -*-
"""手动验证脚本：调用百炼「千问图像模型」原生同步接口生成一张图（与线上代码同一路径）。

用法（在 backend 目录下）：python test.py
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv()

model = os.getenv("DASHSCOPE_IMAGE_MODEL")
api_key = os.getenv("DASHSCOPE_IMAGE_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
base_url = (
    os.getenv("DASHSCOPE_IMAGE_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL") or ""
).rstrip("/")
image_size = os.getenv("IMAGE_GEN_SIZE")

# 兼容模式地址 / 裸域名 -> 原生 API 根地址（与 image_service 归一化规则一致）
if base_url.endswith("/compatible-mode/v1"):
    base_url = base_url[: -len("/compatible-mode/v1")] + "/api/v1"
if not base_url.endswith("/api/v1"):
    base_url = f"{base_url}/api/v1"

url = f"{base_url}/services/aigc/multimodal-generation/generation"

payload = {
    "model": model,
    "input": {
        "messages": [{"role": "user", "content": [{"text": "一只可爱的小狗，写实，高清"}]}]
    },
    "parameters": {
        "size": image_size,
        "n": 1,
        "prompt_extend": True,
        "watermark": False,
    },
}
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
}

print(f"请求地址：{url}")
print(f"模型：{model}    尺寸：{image_size}")
print("开始调用生图接口，超时设置120秒...")
try:
    resp = requests.post(url, json=payload, headers=headers, timeout=120)
    print(f"状态码: {resp.status_code}")
    data = resp.json()
    if resp.status_code == 200:
        image_url = data["output"]["choices"][0]["message"]["content"][0]["image"]
        print("生图成功，图片地址（24 小时有效）：")
        print(image_url)
    else:
        print("生图失败，返回：")
        print(data)
except requests.exceptions.ReadTimeout:
    print("读取超时：服务长时间没有返回结果，检查 MaaS 模型实例状态！")
except requests.exceptions.ConnectionError:
    print("网络连接失败：域名/网络不通")
except Exception as e:
    print(f"其他异常：{type(e)} - {str(e)}")
