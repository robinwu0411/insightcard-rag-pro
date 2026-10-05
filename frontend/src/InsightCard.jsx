import React from 'react'

const STATUS_COLORS = {
  green: '#28a745', yellow: '#ffc107', orange: '#fd7e14', red: '#dc3545'
}

export default function InsightCard({ data }) {
  const statusColor = STATUS_COLORS[data.alert_level] || '#666'
  const gapColor = data.gap_pct > 15 ? '#dc3545' : data.gap_pct > 5 ? '#fd7e14' : '#ffc107'

  return (
    <div style={{
      borderRadius: 16, overflow: 'hidden',
      border: '1px solid #e0e0e0', background: 'white',
      boxShadow: '0 2px 8px rgba(0,0,0,0.06)'
    }}>
      <div style={{ padding: 20, borderBottom: '1px solid #f0f0f0' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <h2 style={{ fontSize: 18, margin: 0 }}>{data.display_name}</h2>
            <p style={{ fontSize: 12, color: '#888', margin: '4px 0 0' }}>
              {data.vendor_id} · {data.period}
            </p>
          </div>
          <div style={{
            padding: '4px 12px', borderRadius: 20, fontSize: 12,
            background: statusColor, color: 'white', fontWeight: 600
          }}>
            {data.status}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', padding: 20, gap: 16 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: '#888' }}>Current</div>
          <div style={{ fontSize: 28, fontWeight: 700 }}>
            {data.value}<span style={{ fontSize: 14, color: '#888' }}> {data.unit}</span>
          </div>
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: '#888' }}>Goal</div>
          <div style={{ fontSize: 28, fontWeight: 600, color: '#666' }}>
            {data.goal}<span style={{ fontSize: 14, color: '#888' }}> {data.unit}</span>
          </div>
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: '#888' }}>Gap</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: gapColor }}>
            {data.gap_pct.toFixed(1)}%
          </div>
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: '#888' }}>Trend</div>
          <div style={{ fontSize: 18, fontWeight: 600, color: data.trend_direction === 'down' ? '#dc3545' : '#28a745' }}>
            {data.trend_direction === 'down' ? '↓' : '↑'} {Math.abs(data.trend_pct).toFixed(1)}%
          </div>
        </div>
      </div>

      {data.contributors_top && data.contributors_top.length > 0 && (
        <div style={{ padding: '0 20px 20px' }}>
          <h4 style={{ fontSize: 13, color: '#888', marginBottom: 8 }}>Top Contributors</h4>
          {data.contributors_top.map((c, i) => (
            <div key={i} style={{
              display: 'flex', justifyContent: 'space-between',
              padding: '6px 0', borderBottom: '1px solid #f5f5f5', fontSize: 13
            }}>
              <span>{c.name}</span>
              <span style={{ color: '#666' }}>{c.value} {data.unit} ({c.contribution_pct > 0 ? '+' : ''}{c.contribution_pct}%)</span>
            </div>
          ))}
        </div>
      )}

      {data.contributors_bottom && data.contributors_bottom.length > 0 && (
        <div style={{ padding: '0 20px 20px' }}>
          <h4 style={{ fontSize: 13, color: '#dc3545', marginBottom: 8 }}>Underperforming</h4>
          {data.contributors_bottom.map((c, i) => (
            <div key={i} style={{
              display: 'flex', justifyContent: 'space-between',
              padding: '6px 0', borderBottom: '1px solid #f5f5f5', fontSize: 13
            }}>
              <span>{c.name}</span>
              <span style={{ color: '#dc3545' }}>{c.value} {data.unit} ({c.contribution_pct > 0 ? '+' : ''}{c.contribution_pct}%)</span>
            </div>
          ))}
        </div>
      )}

      {data.retrieved_sources && data.retrieved_sources.length > 0 && (
        <div style={{ padding: '0 20px 20px' }}>
          <h4 style={{ fontSize: 13, color: '#888', marginBottom: 8 }}>Knowledge Sources</h4>
          {data.retrieved_sources.map((s, i) => (
            <div key={i} style={{ fontSize: 11, color: '#999', padding: '2px 0' }}>
              [{s.rank}] {s.source} > {s.section} (semantic: {s.semantic_score}, rerank: {s.rerank_score})
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
