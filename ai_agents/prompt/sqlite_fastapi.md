请你基于目前的代码,继续帮我更新fastapi中sqlite的部分:
目前写入的方法是:
post
http://host.docker.internal:8000/api/visitors

{
  "license_plate": "{{#conversation.license_plate#}}",
  "company": "{{#conversation.company#}}",
  "phone": "{{#conversation.phone_number#}}",
   ....等其他信息
}
请基于 FastAPI + SQLite 开发一个访客登记队列服务，用于配合 Dify Workflow 和微信机器人使用。

一、技术要求

1. 使用 Python FastAPI。
2. 使用 SQLite 作为数据库。
3. 数据库文件路径可配置，例如默认使用 ./data/app.db。
4. 服务端口使用 8000。
5. FastAPI 启动时需要自动创建 visitors 表。
6. 所有接口返回 JSON。
7. 时间字段统一使用字符串格式：YYYYMMDDHHMMSS，例如 20260530090000。
8. 队列顺序按照 entry_time 升序排列；如果 entry_time 相同，则按照 id 升序排列。
9. status 只允许三个值：
   - 0：待确认
   - 1：录入完成
   - 2：已删除

二、数据库表设计

表名：visitors

字段如下：

- id：INTEGER PRIMARY KEY AUTOINCREMENT，自增 ID
- license_plate：TEXT，车牌号，必填
- entry_time：TEXT，到访时间，格式 YYYYMMDDHHMMSS，必填
- purpose：TEXT，到访事由，必填
- company：TEXT，单位/公司，必填
- phone：TEXT，手机号，必填
- name：TEXT，访客姓名，必填
- status：INTEGER，状态，必填，0=待确认，1=录入完成，2=已删除
- delete_reason：TEXT，删除原因，仅 status=2 时有值
- deleted_at：TEXT，删除时间，格式 YYYYMMDDHHMMSS，仅 status=2 时有值

三、核心业务规则

1. Dify Chatflow 收集完整访客信息后，调用创建访客接口，将数据写入 visitors 表，status 默认为 0。
2. Dify Workflow 查询队头时，调用：
   Method: GET
   URL: /api/visitors/queue/head
3. 队头定义：
   在 visitors 表中查询 status=0 的记录，按 entry_time ASC, id ASC 排序，取第一条。
4. 用户在微信回复“确认”时，系统确认当前队头：
   将当前队头的 status 从 0 更新为 1。
5. 用户在微信回复“删除：原因”时，系统删除当前队头：
   将当前队头的 status 从 0 更新为 2，并写入 delete_reason 和 deleted_at。
6. 删除操作必须提供 delete_reason，不能为空。
7. 不允许确认或删除非队头记录。
8. 不需要 status=3。
9. 当前待处理数量为 status=0 的总数。
10. 后续待处理数量为 status=0 的总数减 1，最小为 0。

四、需要实现的接口

1. 创建访客记录

Method: POST
Path: /api/visitors/create

请求 JSON：

{
  "license_plate": "浙C079DU",
  "entry_time": "20260531034353",
  "purpose": "送水",
  "company": "蓝色蚂蚁",
  "phone": "18858807766",
  "name": "张天乐"
}

处理逻辑：

- 校验所有字段必填。
- 校验 entry_time 必须是 14 位数字字符串。
- 插入 visitors 表。
- status 默认写入 0。
- delete_reason 和 deleted_at 默认为空。
- 返回新建记录的 visitor_id。

成功返回示例：

{
  "success": true,
  "message": "visitor created",
  "visitor_id": 8,
  "status": 0
}

失败返回示例：

{
  "success": false,
  "message": "missing required field: license_plate"
}

2. 查询当前队头

Method: GET
Path: /api/visitors/queue/head

这是 Dify Workflow 当前使用的接口，请确保可通过以下地址访问：

http://host.docker.internal:8000/api/visitors/queue/head

处理逻辑：

- 查询 status=0 的记录。
- 按 entry_time ASC, id ASC 排序。
- 取第一条作为当前队头。
- 统计 status=0 的总数 pending_count。
- 计算 waiting_count = max(pending_count - 1, 0)。
- 返回扁平 JSON，避免 Dify 无法读取嵌套字段。

有队头时返回：

{
  "success": true,
  "has_item": "true",
  "pending_count": "3",
  "waiting_count": "2",
  "visitor_id": "8",
  "license_plate": "浙C079DU",
  "entry_time": "20260531034353",
  "purpose": "送水",
  "company": "蓝色蚂蚁",
  "phone": "18858807766",
  "name": "张天乐",
  "status": "0"
}

无队头时返回：

{
  "success": true,
  "has_item": "false",
  "pending_count": "0",
  "waiting_count": "0",
  "visitor_id": "",
  "license_plate": "",
  "entry_time": "",
  "purpose": "",
  "company": "",
  "phone": "",
  "name": "",
  "status": ""
}

注意：
- has_item、pending_count、waiting_count、visitor_id、status 建议都返回字符串，方便 Dify 条件分支和变量引用。
- 不要返回嵌套 visitor 对象，全部字段扁平化返回。

3. 确认当前队头

Method: POST
Path: /api/visitors/queue/confirm

请求 JSON 可以为空对象：

{}

处理逻辑：

- 查询当前队头，也就是 status=0 中 entry_time 最早、id 最小的记录。
- 如果没有队头，返回 success=false。
- 如果有队头，将该记录 status 更新为 1。
- 返回被确认的 visitor_id 和记录信息。

成功返回示例：

{
  "success": true,
  "message": "visitor confirmed",
  "visitor_id": "8",
  "license_plate": "浙C079DU",
  "entry_time": "20260531034353"
}

没有待确认记录时返回：

{
  "success": false,
  "message": "no pending visitor"
}

4. 删除当前队头

Method: POST
Path: /api/visitors/queue/delete

请求 JSON：

{
  "delete_reason": "车牌信息错误"
}

处理逻辑：

- 校验 delete_reason 必填且不能为空。
- 查询当前队头，也就是 status=0 中 entry_time 最早、id 最小的记录。
- 如果没有队头，返回 success=false。
- 如果有队头，将该记录 status 更新为 2。
- 写入 delete_reason。
- 写入 deleted_at，格式 YYYYMMDDHHMMSS。
- 返回被删除的 visitor_id 和删除原因。

成功返回示例：

{
  "success": true,
  "message": "visitor deleted",
  "visitor_id": "8",
  "delete_reason": "车牌信息错误",
  "deleted_at": "20260531035022"
}

缺少删除原因时返回：

{
  "success": false,
  "message": "delete_reason is required"
}

没有待确认记录时返回：

{
  "success": false,
  "message": "no pending visitor"
}

5. 查询全部访客记录，调试用

Method: GET
Path: /api/visitors

返回 visitors 表所有记录，按 id DESC 排序。

6. 重置测试数据，调试用

Method: POST
Path: /api/visitors/test/reset

处理逻辑：

- 清空 visitors 表。
- 插入几条测试数据。
- 至少包含 3 条 status=0 的待确认数据。
- 返回插入数量。

五、微信推送消息格式

Dify Workflow 调用 /api/visitors/queue/head 后，如果 has_item == "true"，再调用微信发送接口。

微信消息内容使用 queue/head 返回的扁平字段生成：

访客登记完成，请您确认！（您后续还有 {waiting_count} 条待处理）

车牌号：{license_plate}
到访时间：{entry_time}
到访事由：{purpose}
单位/公司：{company}
手机号：{phone}
访客姓名：{name}

回复“确认”完成录入。
回复“删除：原因”删除该登记。

六、测试用例

请实现完成后，用 curl 给出以下测试命令。

1. 重置测试数据

POST http://localhost:8000/api/visitors/test/reset

预期：
返回 success=true，并插入至少 3 条 status=0 数据。

2. 查询队头

GET http://localhost:8000/api/visitors/queue/head

预期：
返回 has_item="true"。
返回 entry_time 最早的 status=0 记录。
waiting_count = pending_count - 1。

3. 确认队头

POST http://localhost:8000/api/visitors/queue/confirm

请求：

{}

预期：
最早的 status=0 记录变为 status=1。
再次查询 queue/head 时，队头变成下一条 status=0 记录。

4. 删除队头

POST http://localhost:8000/api/visitors/queue/delete

请求：

{
  "delete_reason": "测试删除"
}

预期：
当前队头 status=2。
delete_reason 写入“测试删除”。
deleted_at 为 14 位时间字符串。
再次查询 queue/head 时，队头变成下一条 status=0 记录。

5. 创建新访客

POST http://localhost:8000/api/visitors/create

请求：

{
  "license_plate": "浙C079DU",
  "entry_time": "20260531034353",
  "purpose": "送水",
  "company": "蓝色蚂蚁",
  "phone": "18858807766",
  "name": "张天乐"
}

预期：
插入 visitors 表，status=0。
如果它的 entry_time 比现有待确认记录更早，则它会成为新的队头。
如果它的 entry_time 更晚，则排在后面。

6. 字段校验

请求缺少 license_plate 或 entry_time 时，应返回 success=false。
entry_time 不是 14 位数字时，应返回 success=false。

七、兼容 Dify 的要求

1. /api/visitors/queue/head 必须返回扁平 JSON，不要返回嵌套对象。
2. has_item 必须返回字符串 "true" 或 "false"。
3. waiting_count 必须返回字符串。
4. 空字段返回空字符串，不返回 null。
5. 所有接口都必须返回 application/json。
6. 服务必须允许从 Dify Docker 容器访问：
   http://host.docker.internal:8000/api/visitors/queue/head
7. FastAPI 启动时需要监听 0.0.0.0，而不是 127.0.0.1。
8. 启动命令示例：
   uvicorn main:app --host 0.0.0.0 --port 8000