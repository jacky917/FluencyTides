import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import App from './App.tsx'
import './index.css'

// 資料在記憶體中保留 30 分鐘（預設 5 分鐘）：切到別頁再回來時直接顯示上次的資料，背景再更新
const queryClient = new QueryClient({ defaultOptions: { queries: { gcTime: 30 * 60 * 1000 } } })

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
)
