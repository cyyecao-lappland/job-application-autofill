# v2 数据契约

旧布尔值审核协议已停用。所有路径用绝对路径；快照/调用记录路径例外，必须在运行目录内，相对该目录解析。

## 配置

```json
{
  "schema": "recruitment-screening-config-v2",
  "target": 30,
  "target_unit": "companies",
  "candidate_files": ["C:/.../iguopin-candidates.json"],
  "profile_path": "C:/.../current-profile.md",
  "criteria_path": "C:/.../screening-preferences.md",
  "denylist_sources": [{
    "role": "haitou_company_exclusion",
    "confirmed_via": "用户消息或已核验的海投应用配置出处",
    "path": "C:/.../actual-haitou.json",
    "records_pointer": "/applications",
    "company_name_pointer": "/company_name",
    "company_id_pointer": "/company_id",
    "aliases_pointer": "/aliases"
  }],
  "company_aliases": [{
    "company_name": "示例公司",
    "company_id": "shared-registry-id",
    "aliases": ["示例公司有限公司", "Example"]
  }],
  "official_roots_path": "C:/.../verified-official-roots.json"
}
```

JSON pointer：顶层数组用空字符串，嵌套字段用 /applications 等；ID/别名可选，无此字段时省略配置。指定了却缺失则报错。不同来源的 company_id 必须属于同一登记体系，不能直接混用各平台数字ID。公司别名人工确认后配置，集团与子公司不自动合并。官网登记表可初始为 []，由主代理逐公司核实后追加：

优先在运行配置中显式提供 denylist_sources；若省略，先读取 references/local-sources.local.json，再回退 references/local-sources.json。公开默认绑定为空，初始化会因缺少已确认来源而停止。个人绑定文件不上传 GitHub；可按 local-sources.json 的 schema 和上面的 denylist_sources 格式创建。不能因为原台账丢失，就将岗位抽取台账或部分恢复快照改名作为完整台账。

langgraph_ledger_v1 专用适配器读取 schema=1 的 applied 字典、in_progress 字典内 job、queue 数组、events.details.company 和 runs.config.excluded_companies。所有状态均按公司存在即排除；不解释为提交成功。平台ID不当作共享公司ID，按公司名和显式别名匹配。程序保留原台账不变，每轮重读并报告revision及来源更新时间。其他JSON仍使用显式pointer，不递归抓取任意 name 字段。

```json
[{
  "company_key": "示例公司",
  "url": "https://official.example/careers",
  "verified_via": "实际核验官网归属的工具调用或来源记录"
}]
```

主代理维护该表，审核子代理不写。company_key 按 normalize_company 生成。不能仅凭域名看似可信就登记为官网。

履历/偏好以原文快照写入 manifest，每次 advance 检查源原文一致；变化则新建运行。偏好应写明本次应聘类型、届别、方向、地点等。原文只保留审核所需信息，勿把身份证、电话等无关敏感资料发给子代理。

## 审核对象

```json
{
  "schema": "recruitment-jd-review-v2",
  "run_id": "由manifest复制",
  "candidate_id": "candidate-1",
  "decision": "accept",
  "reason": "根据具体职责与资格的判断",
  "reviewed_at": "带时区的实际时间",
  "invocation_file": "receipts/candidate-1.json",
  "eligibility": "pass",
  "jd_source_url": "https://official.example/job/123",
  "identity": {
    "company_name": "与候选完全一致",
    "job_name": "与候选完全一致",
    "official_job_id": "123"
  },
  "pages": [
    {
      "url": "https://official.example/careers",
      "tool_call_id": "真实浏览器读取调用ID",
      "observed_at": "带时区的实际时间",
      "snapshot_file": "evidence/candidate-1-root.txt"
    },
    {
      "url": "https://official.example/job/123",
      "tool_call_id": "真实浏览器读取调用ID",
      "observed_at": "带时区的实际时间",
      "snapshot_file": "evidence/candidate-1-jd.txt"
    }
  ],
  "opening": {
    "status": "open",
    "quote": "快照中的在招/申请原文",
    "apply_label": "申请职位",
    "apply_url": "https://official.example/apply/123",
    "apply_enabled": true,
    "deadline_kind": "not_stated"
  },
  "checks": [
    {
      "field": "graduation_year",
      "result": "pass",
      "jd_quote": "快照中的准确原文",
      "profile_quote": "履历中的准确原文",
      "criteria_quote": "筛选偏好中的准确原文",
      "reason": "为什么满足"
    }
  ],
  "all_hard_requirements_reviewed_reason": "其他院校、证书、成绩、工作年限等硬门槛的完整审阅说明"
}
```

checks 必须覆盖 location、graduation_year、degree、major、language、experience、other_hard_gates、role_fit，各字段只出现一次。每个 pass 必须引用快照/履历/偏好原文。只有 language、experience、other_hard_gates 可用 not_required，必须解释完整JD中未要求该门槛。未知或失败应返回 hold/reject，不能把硬性条件缺失算通过。允许附加具体门槛检查，附加项也不能失败/未知。

dated 截止时间必须附 deadline_at（带时区）和 deadline_quote；rolling 或 not_stated 需要当前可用申请入口证据，不能编造截止时间。动态按钮没有URL或没有官方岗位编号时返回 hold，请主代理核验可审计的岗位身份/链接后另起运行，禁止伪造字段凑齐。

官网链每一步目标地址必须出现在前一步浏览器原始快照中；跳转平台须保存链接/重定向实际输出。页面末端必须包含准确公司名、职位名、官方岗位编号；聚合站ID可能不同，official_job_id 使用官网ID。身份无法对应则 hold。

## 主代理调用收据

```json
{
  "run_id": "同上",
  "candidate_id": "candidate-1",
  "model": "gpt-6-luna",
  "agent_id": "实际spawn结果",
  "spawn_call_id": "主代理实际spawn工具调用ID",
  "result_call_id": "主代理实际获取完成结果的工具调用ID",
  "started_at": "实际派发时间，带时区",
  "browser_calls": ["本次实际浏览器读取/失败调用ID"]
}
```

该收据由主代理读取实际调用结果后保存，不能让 Luna 自报一个模型名代替。hold/reject 同样需要 Luna 调用、时间与浏览器尝试记录，防止用虚构审核消耗候选。

## 持久化及结束

manifest.json 为固定输入；每次 advance 写新轮，前轮不覆盖。只接受上一轮 next_for_review 中的一条，拒绝重复/陌生审核。最新轮 contains accepted、next_for_review、reviews、dispositions、最新排除源mtime/count。complete 表示达到目标；needs_review 表示继续派发；source_exhausted 表示无更多候选且有缺口。

通过证据必须在本运行/Luna调用开始后采集且不超过24小时，主代理交付前再 advance。受最新排除源或时间影响的旧通过项会移除并继续补抽。24小时是最大年龄限制，仍需提交前重新查看岗位。没有哈希操作；这些校验是可追踪性与一致性检查，不能抵御人为修改全部本地证据。
