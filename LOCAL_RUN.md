# 校园午餐项目本地运行

项目基于 [shuhongfan/sky-take-out](https://github.com/shuhongfan/sky-take-out)，上游基准提交为`267313f948bba2001fbc497995a39ef61f2c621f`。发布仓库包含后端、Vue源码、编译后的小程序工程、初始SQL与Windows Nginx运行文件；新增校园午餐预订、自提核销及订单可靠性改造。课程材料、上游旧前端构建、私有配置、运行数据及旧Git历史不随发布仓库上传。

## 启动与访问

在仓库根目录的PowerShell中运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/bootstrap.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-front.ps1 -Install
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start.ps1 -Build
```

当前机器已准备好工具、数据库及Vue构建资源，日常直接运行`start.ps1`即可。修改后端后先停止Java进程再打包，避免Windows锁定正在运行的JAR；停止脚本会保留数据库文件。

| 内容 | 地址或配置 |
| --- | --- |
| 学生预订页 | http://127.0.0.1:8081/#/campus |
| 商家餐次与核销页 | http://127.0.0.1:8081/#/meal |
| 原管理端 | http://127.0.0.1:8081 |
| 管理员账号 | `admin / 123456`，教学SQL中的本地账号 |
| 学生演示账号 | 页面登录码`local-demo`或`local-second` |
| 接口文档 | http://127.0.0.1:8080/doc.html |
| 后端 | Spring Boot 2.7.3、JDK 8、端口8080 |
| 数据库 | MySQL 8.0.46，127.0.0.1:3306，`sky_take_out` |
| 数据表 | 原11张，加餐次与退款任务2张，以及Flyway历史表，共14张 |
| Redis | 7.4.3，127.0.0.1:6379，逻辑库10 |
| 工具与数据 | 仓库同级`.runtime`；可用`SKY_RUNTIME_DIR`调整 |
| 构建资源与日志 | 仓库内`.local`，已忽略 |

脚本不安装Windows服务，不修改系统Java或PATH；后台进程以隐藏窗口启动。本地密码和JWT密钥自动生成，保存于`.runtime/credentials.json`。bootstrap仅在没有业务表时导入初始SQL；Flyway在已有数据库上基线为V1，顺序执行V2至V5，再次启动保留数据且验证迁移记录。

真实故障及双实例实验使用独立测试库、独立Redis和持久化模拟端，不需要启动正常8080/8081服务。完整命令与故障开关见[可靠性迭代](docs/RELIABILITY_ITERATION.md)。故障屏障默认关闭；正常local演示未配置独立模拟端时仍使用轻量模拟，不能用该模式推断资金去重能力。

## 演示步骤

1. 管理员登录，在“午餐预订与核销”发布餐次，填写容量、截单时间和自提时段。当前时间必须早于截单，截单早于自提开始，结束晚于开始，餐次处于未来7天内。
2. 原管理端设置营业状态为营业；学生页使用`local-demo`登录，选择餐次和菜品，填写取餐人和手机号并提交预订。
3. 页面展示服务器计算的金额，再点击“模拟支付”。餐盒费每份1元，自提配送费为0；服务端重新读取菜单价格和销售状态。
4. 商家查看餐次订单与备餐汇总，依次接单、标记备餐就绪。学生向工作人员出示22位取餐码，在该餐次自提时段内核销。
5. 截单前且尚未接单，学生可取消；已支付订单进入退款任务，确认退款后显示已退款。商家可以查看失败任务并人工重试。

演示核销时可以把截单与自提时间设置在未来几分钟；服务器统一使用Asia/Shanghai。系统不会在夜间把所有订单直接改为完成。

页面会保存一笔预订的请求键和原请求，网络失败可“恢复/重试上次预订”；开始另一笔使用“开始下一笔预订”。复用旧请求键会返回旧订单，不能把它当成新的结算。

## API与本地适配

所有用户接口除登录和店铺状态外需要`authentication`请求头；管理接口需要`token`请求头。Nginx把`/api/`代理到后端`/admin/`。

| 用途 | 通过8081访问的接口 |
| --- | --- |
| 学生登录 | `POST /user/user/login`，`{"code":"local-demo"}` |
| 可预订餐次 | `GET /user/meal/sessions` |
| 服务端报价 | `GET /user/order/quote?mealSessionId={id}` |
| 自提下单 | `POST /user/order/submit`，包含requestKey、mealSessionId、contactName、contactPhone |
| 配送下单 | 同一接口，包含requestKey与addressBookId |
| 本地支付 | `POST /user/local/orders/{订单号}/pay` |
| 餐次管理 | `GET/POST /api/meal/sessions` |
| 备餐汇总 | `GET /api/meal/sessions/{id}/preparation` |
| 餐次订单 | `GET /api/meal/sessions/{id}/orders` |
| 备餐就绪 | `PUT /api/meal/orders/{id}/ready` |
| 取餐核销 | `POST /api/meal/redeem`，`{"pickupCode":"取餐码"}` |
| 退款任务及重试 | `GET /api/refunds`、`POST /api/refunds/{任务id}/retry` |

local profile使用模拟微信授权、支付及退款，本地文件上传替代OSS，跳过地图距离查询。数据库、Redis与订单事务真实执行。测试用户登录码支持`local-test-`加1至20位小写字母、数字或连字符。以下诊断入口仅注册在local中，且要求对应JWT：

- 用户`POST /user/local/orders/{number}/callback`，带paidAmount，用于重复或迟到通知测试，仍校验订单归属。
- 管理`POST /api/local/timeouts/{id}`，只取消已经满足超时规则的订单。
- 管理`POST /api/local/refunds/run`，执行到期退款任务。
- 管理`POST /api/local/refunds/fail-next`，带`{"count":1}`，模拟下一次退款调用失败。

非local支付通知会校验平台证书、原始正文签名、AES-GCM解密结果和订单金额。真实微信授权、支付、退款、证书轮换、OSS、地图仍需配置及真实联调。本地模拟结果不代表真实资金平台验收。浏览器连接不可用，本次完成了构建、资源和业务接口检查，尚未做页面自动点击验收。

仓库的`mp-weixin`是编译产物，本次补充其下单请求键兼容，验证了网络失败时保留原请求；校园新页面的可维护源码位于Vue工程。

## 构建与验证

```powershell
# 后端改动后先停止服务，保留MySQL和Redis
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stop.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start.ps1 -Build

# 首次安装前端依赖，再构建Vue源码；本机已安装可省略-Install
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-front.ps1 -Install

# 运行中的服务通过Nginx做真实业务检查
python scripts/smoke.py
python scripts/feature_checks.py
node scripts/check-wechat-request.cjs

# 完整停止后重启，再验证幂等持久化与数据保留
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/stop.ps1 -All
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start.ps1
python scripts/feature_checks.py --replay-only
```

测试仅用于本套本地演示环境，会创建测试用户、地址、餐次和订单。故障检查会修改自己的时间及任务状态，临时修改选定菜品的价格/启售状态并在finally恢复；事务回滚检查会创建临时触发器并在finally删除。不要把这些脚本指向共享环境。结果保存在`.local/smoke-report.json`和`.local/feature-report.json`。

前端npm锁文件中的旧淘宝下载URL已换成npm官方URL，依赖版本与完整性校验保持原样。旧Webpack的Node兼容参数仅在构建进程中设置；构建仍有资源体积告警。start提供`.local/frontend`中的源码构建结果，未构建时给出明确提示。

检查结果见[验证记录](docs/VALIDATION.md)，三份介绍见[简历与面试材料](docs/PROJECT_INTRODUCTIONS.md)，设计说明见[技术设计](docs/TECHNICAL_DESIGN.md)。
