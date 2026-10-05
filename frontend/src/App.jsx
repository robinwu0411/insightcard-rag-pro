import React, { useState, useEffect, useRef } from 'react'
import InsightCard from './InsightCard.jsx'

const METRICS = [
  { name: 'net_ppm', label: 'Net PPM' },
  { name: 'revenue', label: 'Shipped Revenue' },
  { name: 'cr', label: 'Conversion Rate' },
  { name: 'in_stock_rate', label: 'In-Stock Rate' },
  { name: 'glance_views', label: 'Glance Views' },
]

export default function App() {
  const [selectedMetric, setSelectedMetric] = useState('net_ppm')
  const [cardData, setCardData] = useState(null)
  const [recommendation, setRecommendation] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [sources, setSources] = useState(null)
  const recommendationRef = useRef(null)

  const fetchInsight = async (metric) => {
    setCardData(null)
    setRecommendation('')
    setStreaming(true)
    setSources(null)

    const sessionId = crypto.randomUUID()
    const url = `/api/insight/stream?vendor_id=toshiba_hl&metric_name=${metric}&period=QTD`
    const response = await fetch(url, {
      headers: { 'X-Session-Id': sessionId }
    })

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          const eventType = line.slice(7)
          continue
        }
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            if (data.card_data) {
              setCardData(data.card_data)
            } else if (data.text) {
              setRecommendation(prev => prev + data.text)
            } else if (data.status === 'complete') {
              setStreaming(false)
            }
          } catch (e) {}
        }
      }
    }
    setStreaming(false)
  }

  useEffect(() => {
    if (recommendationRef.current) {
      recommendationRef.current.scrollTop = recommendationRef.current.scrollHeight
    }
  }, [recommendation])

  return (
    <div style={{ fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif', maxWidth: 900, margin: '0 auto', padding: 24 }}>
      <h1 style={{ fontSize: 24, marginBottom: 4 }}>InsightCard RAG Pro</h1>
      <p style={{ color: '#666', fontSize: 14, marginBottom: 24 }}>Vendor Performance Insight Card with RAG-powered recommendations</p>

      <div style={{ display: 'flex', gap: 8, marginBottom: 24, flexWrap: 'wrap' }}>
        {METRICS.map(m => (
          <button
            key={m.name}
            onClick={() => { setSelectedMetric(m.name); fetchInsight(m.name) }}
            style={{
              padding: '8px 16px', borderRadius: 8, border: '1px solid #ddd',
              background: selectedMetric === m.name ? '#185FA5' : 'white',
              color: selectedMetric === m.name ? 'white' : '#333',
              cursor: 'pointer', fontSize: 13
            }}
          >
            {m.label}
          </button>
        ))}
      </div>

      {cardData && <InsightCard data={cardData} />}

      {(recommendation || streaming) && (
        <div style={{
          marginTop: 16, padding: 20, borderRadius: 12,
          background: '#f8f9fa', border: '1px solid #e0e0e0',
          maxHeight: 500, overflow: 'auto'
        }} ref={recommendationRef}>
          <h3 style={{ fontSize: 14, color: '#666', marginBottom: 12 }}>AI Recommendation</h3>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 14, lineHeight: 1.6, fontFamily: 'inherit', margin: 0 }}>
            {recommendation}
            {streaming && <span style={{ opacity: 0.5 }}>▋</span>}
          </pre>
        </div>
      )}
    </div>
  )
}
