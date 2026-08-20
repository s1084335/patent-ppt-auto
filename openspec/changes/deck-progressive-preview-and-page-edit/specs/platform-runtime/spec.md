# Delta Spec: platform-runtime — SSE 逐頁事件型別

## ADDED Requirements

### Requirement: `deck_page` 事件型別

SSE 通道 SHALL 支援 `deck_page` 事件型別，走既有的
`DB trigger → pg_notify → FastAPI SSE` 管道。

#### Scenario: 沿用既有管道不新增服務

- **WHEN** 實作逐頁事件
- **THEN** SHALL 走既有 `LISTEN patent_events` 通道
- **AND** SHALL NOT 新增獨立的預覽伺服器或連線
- ⚠ ppt-master 的作法是另起 Flask 服務（`svg_editor` 5,234 行）；
  我們既有 SSE 已在推 AI job 進度，加一個事件型別即可

#### Scenario: payload 大小受 NOTIFY 上限約束

- **WHEN** 組裝 `deck_page` 事件 payload
- **THEN** payload SHALL 保持在 PostgreSQL `NOTIFY` 的 8000 bytes 上限內
- **AND** SHALL 以資產相對路徑取代內嵌位元組

#### Scenario: 斷線退化不變

- **WHEN** SSE 斷線
- **THEN** 逐頁事件 SHALL 沿用既有的 30 秒輪詢保底機制
- ⚠ 不為新事件另訂一套退化策略——那會變成兩套斷線行為
