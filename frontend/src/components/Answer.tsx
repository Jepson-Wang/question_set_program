import ReactMarkdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'

// 回答是模型生成的自由文本：按 Markdown 渲染，支持 $…$ 公式。
// react-markdown 默认不渲染原始 HTML，模型输出里夹带的 <script> 不会执行
export default function Answer({ text }: { text: string }) {
  return (
    <div className="answer">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
        {text}
      </ReactMarkdown>
    </div>
  )
}
