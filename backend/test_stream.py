import requests
import json

# 1. 替换 JWT Token
TOKEN = "your_token_here"

url = "http://127.0.0.1:8000/agent/analyse/stream"
headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}
payload = {
    "text": "帮我出一道关于一元二次方程的题"
}

print("开始接收流式数据：\n")

# 使用 stream=True 开启流式接收
with requests.post(url, headers=headers, json=payload, stream=True) as response:
    if response.status_code != 200:
        print(f"请求失败，状态码：{response.status_code}, {response.text}")
    else:
        # 逐行读取服务器推过来的数据
        for line in response.iter_lines():
            if line:
                # 解码成字符串
                decoded_line = line.decode('utf-8')
                # 过滤掉 'data: ' 前缀
                if decoded_line.startswith("data: "):
                    data_str = decoded_line[6:]

                    if data_str == "[DONE]":
                        print("\n\n[输出结束]")
                        break

                    # 解析 JSON 并打印内容，end="" 让它不换行，像打字机一样
                    try:
                        data_json = json.loads(data_str)
                        print(data_json.get("content", ""), end="", flush=True)
                    except json.JSONDecodeError:
                        pass