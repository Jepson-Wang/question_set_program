<template>
  <div class="chat-page">
    <!-- 顶部栏 -->
    <div class="chat-header">
      <div class="header-left">
        <h1>QuestionForge</h1>
        <p>智能题目生成平台</p>
      </div>

      <div class="header-right">
        <el-button plain @click="handleMockUpload">上传文件</el-button>
        <el-button type="danger" plain @click="handleLogout">退出登录</el-button>
      </div>
    </div>

    <!-- 聊天主体 -->
    <div class="chat-main">
      <div class="message-box" ref="messageBoxRef">
        <template v-if="messageList.length > 0">
          <div
            v-for="item in messageList"
            :key="item.id"
            class="message-row"
            :class="item.role === 'user' ? 'user-row' : 'assistant-row'"
          >
            <div
              class="message-bubble"
              :class="item.role === 'user' ? 'user-bubble' : 'assistant-bubble'"
            >
              <div class="message-role">
                {{ item.role === 'user' ? '我' : 'AI' }}
              </div>
              <div class="message-content">
                {{ item.content }}
              </div>
            </div>
          </div>
        </template>

        <el-empty
          v-else
          description="还没有消息，开始提问吧"
        />
      </div>

      <!-- 输入区 -->
      <div class="chat-input-area">
        <el-input
          v-model="inputValue"
          type="textarea"
          :rows="3"
          resize="none"
          placeholder="请输入你的问题..."
          @keyup.enter="handleEnter"
        />
        <div class="input-actions">
          <span class="tips">按 Enter 发送，Shift + Enter 换行</span>
          <el-button type="primary" @click="handleSend">发送</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { nextTick, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { removeToken } from '../utils/auth'

const router = useRouter()

const inputValue = ref('')
const messageBoxRef = ref(null)

const messageList = ref([
  {
    id: 1,
    role: 'assistant',
    content: '你好，我是 QuestionForge 助手。你可以输入题目需求，我会帮你生成内容。'
  }
])

// 滚动到底部
const scrollToBottom = async () => {
  await nextTick()
  if (messageBoxRef.value) {
    messageBoxRef.value.scrollTop = messageBoxRef.value.scrollHeight
  }
}

// 发送消息
const handleSend = async () => {
  const text = inputValue.value.trim()

  if (!text) {
    ElMessage.warning('请输入内容后再发送')
    return
  }

  // 先追加用户消息
  messageList.value.push({
    id: Date.now(),
    role: 'user',
    content: text
  })

  const userText = text
  inputValue.value = ''

  await scrollToBottom()

  // 临时模拟 AI 回复
  setTimeout(async () => {
    messageList.value.push({
      id: Date.now() + 1,
      role: 'assistant',
      content: `你刚刚输入的是：${userText}。这里先做模拟回复，后面再接真实聊天接口。`
    })

    await scrollToBottom()
  }, 500)
}

// 回车发送
const handleEnter = (event) => {
  if (event.shiftKey) return
  event.preventDefault()
  handleSend()
}

// 退出登录
const handleLogout = async () => {
  try {
    await ElMessageBox.confirm(
      '确定要退出登录吗？',
      '提示',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    // 删除 token
    removeToken()

    ElMessage.success('已退出登录')

    // 回到登录页
    router.push('/login')
  } catch (error) {
    // 用户点了取消，不处理
  }
}

// 上传文件占位
const handleMockUpload = () => {
  ElMessage.info('文件上传功能后续接入')
}
</script>
<style scoped>
.chat-page {
  min-height: 100vh;
  background: #f5f7fa;
  display: flex;
  flex-direction: column;
}

.chat-header {
  height: 72px;
  padding: 0 24px;
  background: #ffffff;
  border-bottom: 1px solid #ebeef5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  box-sizing: border-box;
}

.header-left h1 {
  margin: 0;
  font-size: 28px;
  color: #111827;
}

.header-left p {
  margin: 4px 0 0;
  font-size: 13px;
  color: #909399;
}

.header-right {
  display: flex;
  gap: 12px;
}

.chat-main {
  flex: 1;
  width: 100%;
  max-width: 1100px;
  margin: 0 auto;
  padding: 20px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.message-box {
  flex: 1;
  min-height: 0;
  background: #ffffff;
  border-radius: 16px;
  padding: 20px;
  box-sizing: border-box;
  overflow-y: auto;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.06);
}

.message-row {
  display: flex;
  margin-bottom: 16px;
}

.user-row {
  justify-content: flex-end;
}

.assistant-row {
  justify-content: flex-start;
}

.message-bubble {
  max-width: 70%;
  padding: 14px 16px;
  border-radius: 14px;
  box-sizing: border-box;
  line-height: 1.7;
  word-break: break-word;
}

.user-bubble {
  background: #409eff;
  color: #ffffff;
  border-bottom-right-radius: 4px;
}

.assistant-bubble {
  background: #f2f6fc;
  color: #303133;
  border-bottom-left-radius: 4px;
}

.message-role {
  font-size: 12px;
  margin-bottom: 6px;
  opacity: 0.8;
}

.message-content {
  font-size: 14px;
}

.chat-input-area {
  background: #ffffff;
  border-radius: 16px;
  padding: 16px;
  box-sizing: border-box;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.06);
}

.input-actions {
  margin-top: 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.tips {
  font-size: 12px;
  color: #909399;
}

@media (max-width: 768px) {
  .chat-header {
    height: auto;
    padding: 16px;
    align-items: flex-start;
    flex-direction: column;
    gap: 12px;
  }

  .header-left h1 {
    font-size: 24px;
  }

  .header-right {
    width: 100%;
  }

  .header-right .el-button {
    flex: 1;
  }

  .chat-main {
    padding: 12px;
  }

  .message-bubble {
    max-width: 85%;
  }

  .input-actions {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }

  .tips {
    text-align: left;
  }
}
</style>