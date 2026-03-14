from agents import build_extract_agent, GraphState

# 加载环境变量
from dotenv import load_dotenv
from pathlib import Path

from nodes import extract_node, question_set_node, planner_node

env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

# 打印环境变量检查
import os

print("API_KEY:", os.getenv('API_KEY') is not None)
print("API_URL:", os.getenv('API_URL') is not None)
print("MODEL_NAME:", os.getenv('MODEL_NAME'))

# 使用直接的题目作为输入
text = "若关于x的一元二次方程x2−(2k+1)x+k2+2k=0有两个实数根x1、x，且满足x12+x22=11，求k的值。"

# state: GraphState = {
#     'input': text,
#     'route': '',
#     'result': '',
#     'extract': {'difficulty': '简单',
#                 'knowledge_points': ['一元二次方程', '根与系数的关系', '判别式', '代数方程求解']
#                 }
# }

state: GraphState = {
    'input': text,
    'route': '',
    'result': '',
    'extract': {}
}

# 获取提取智能体




try:
    # 执行提取操作，传递完整的state字典
    extract_result = planner_node(state)
    result = extract_result.get('route')
    # 处理并打印结果
    print("\n原始结果:", result)

    # 尝试提取AI消息内容
    if 'messages' in result:
        for message in result['messages']:
            if hasattr(message, 'content'):
                print("\nAI回复内容:")
                print(message.content)
except Exception as e:
    print(f"\n错误信息: {e}")
    import traceback

    traceback.print_exc()
