import { useEffect, useState } from 'react'
import { Tabs, Card, Spin, Typography, Button, message, Space, Tag } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { monitorApi } from '../api'

const { Text, Paragraph } = Typography

export default function MonitorPage() {
  const [healthz, setHealthz] = useState<any>(null)
  const [readyz, setReadyz] = useState<any>(null)
  const [metrics, setMetrics] = useState<string>('')
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const [h, r, m] = await Promise.all([
        monitorApi.healthz(),
        monitorApi.readyz(),
        monitorApi.metrics(),
      ])
      setHealthz(h)
      setReadyz(r)
      setMetrics(m)
    } catch (e: any) {
      message.error(e?.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  return (
    <Spin spinning={loading}>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
        <Text type="secondary">后端探针与 Prometheus 指标实时拉取</Text>
      </Space>
      <Tabs
        items={[
          {
            key: 'readyz',
            label: '就绪探针 (readyz)',
            children: (
              <Card size="small">
                <pre style={{ margin: 0, maxHeight: 400, overflow: 'auto', fontSize: 12 }}>
                  {JSON.stringify(readyz, null, 2)}
                </pre>
              </Card>
            ),
          },
          {
            key: 'healthz',
            label: '存活探针 (healthz)',
            children: (
              <Card size="small">
                <pre style={{ margin: 0, maxHeight: 400, overflow: 'auto', fontSize: 12 }}>
                  {JSON.stringify(healthz, null, 2)}
                </pre>
              </Card>
            ),
          },
          {
            key: 'metrics',
            label: <span>Prometheus 指标 <Tag color="blue">/metrics</Tag></span>,
            children: (
              <Card size="small">
                <Paragraph>
                  <Text type="secondary">直接展示 Prometheus 文本格式，可复制到 Grafana / 自建监控</Text>
                </Paragraph>
                <pre style={{ margin: 0, maxHeight: 560, overflow: 'auto', fontSize: 12, background: '#f6f8fa', padding: 12, borderRadius: 4 }}>
                  {metrics}
                </pre>
              </Card>
            ),
          },
        ]}
      />
    </Spin>
  )
}
