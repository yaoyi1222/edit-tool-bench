# NodeBB Privilege Types：legacy fuzzy edit 的局部成功如何放大全局错误

## 结论摘要

这个任务是本实验中最能说明“edit 工具影响模型表现”的案例之一。`legacy` 方案使用 OpenCode 原生 fuzzy `edit` 工具，primary run 失败；对同一任务再次使用 `legacy` 重跑仍然失败。而 `exact`、`anchor`、`lsp_symbol` 和 `patch` 的结果都是 pass。

关键点不是 legacy 工具崩溃，也不是模型没有动代码。相反，legacy run 中 24 次原生 `edit` 调用全部以 fuzzy 策略完成，没有 edit-tool error。问题在于：这些“局部编辑成功”的信号让模型相信跨文件方案已经完成，随后模型把 TODO 标为完成并给出完成总结；但官方 evaluator 在同一功能点 `spawnPrivilegeStates` 上失败。

这说明 fuzzy edit 的风险不是简单的“工具调用失败率高”，而是更隐蔽的“局部成功信号太顺滑”。它能降低 old string 不精确时的摩擦，但也会让模型少一次重新核对当前文件、输出契约和测试预期的机会。

## 任务与结果

任务：`instance_NodeBB__NodeBB-f1a80d...`

任务涉及 NodeBB 权限系统，需要把 privilege 按类型组织，并让 server-side privilege map、controller/API payload、Benchpress template、client-side filtering、copy privilege 逻辑保持一致。

结果对照：

| arm | result |
| --- | --- |
| `legacy` | fail |
| `exact` | pass |
| `anchor` | pass |
| `lsp_symbol` | pass |
| `patch` | pass |

关键 metrics：

| run | tokens | tool calls | edit-related calls | result |
| --- | ---: | ---: | --- | --- |
| `legacy` primary | 4.08M | 81 | 24 `edit`, all fuzzy-completed | fail |
| `legacy` retry | 5.37M | 78 | 28 `edit` | fail |
| `exact` primary | 6.31M | 81 | 32 `replace_exact`, 3 `edit_transaction` | pass |

这个对照非常重要：同一任务不是不可解；legacy 也不是只差一次随机采样。至少在这组运行中，legacy 的交互方式更容易把模型带到错误的完成状态。

## 原始行为链路

下面是从原始事件流压缩出的行为 transcript。这里隐去了事件系统字段，只保留模型行为、工具反馈和 evaluator 结果。

原始上下文证据文件：[raw-context/nodebb-legacy-context-excerpt.md](raw-context/nodebb-legacy-context-excerpt.md)

```text
[model]
Now I have a comprehensive understanding of the codebase. Let me implement the changes.

[legacy edit tool: /app/src/privileges/categories.js]
Edit applied successfully. (editStrategy=fuzzy)

[model]
Let me start implementing. First, I'll update the privilege maps and add `getType`/`getPrivilegesByFilter` methods:

[legacy edit tool: /app/src/privileges/categories.js]
Edit applied successfully. (editStrategy=fuzzy)

[model]
Now update the `list` method in categories.js to include `labelData`, `filterTypes`, and propagate types:

[legacy edit tool: /app/public/src/modules/helpers.common.js]
Edit applied successfully. (editStrategy=fuzzy)

[model]
Now update `spawnPrivilegeStates` to add `data-type` attribute:

[legacy edit tool: /app/public/src/modules/helpers.common.js]
Edit applied successfully. (editStrategy=fuzzy)

[model]
Now I understand the template engine. Let me update `spawnPrivilegeStates` to accept types as a third argument, and update the templates accordingly:

[legacy edit tool: /app/public/src/admin/manage/privileges.js]
Edit applied successfully. (editStrategy=fuzzy)

[model]
Now update the client-side JS to use `data-type` filtering instead of column indices:

[todo state]
completed Update client-side JS filtering logic, spawnPrivilegeStates, and copying logic
completed Final verification: check all modified files are consistent and correct

[model]
All changes are complete. Here's a summary ...
`public/src/modules/helpers.common.js` - `spawnPrivilegeStates()` accepts optional third `privTypes` argument and adds `data-type` attribute to each privilege cell, defaulting to `'other'`

[official evaluator]
test/template-helpers.js | helpers should spawn privilege states: FAILED
AssertionError:
actual   <td data-privilege="find" data-type="viewing" data-value="true">
expected <td data-privilege="find" data-value="true" data-type="viewing">

legacy resolved=false
exact resolved=true
```

## 逐步分析

### 1. 模型先形成了正确的大方向

模型识别到任务不只是改一个文件，而是需要联动：

- privilege map 增加 type 信息；
- list payload 暴露 `labelData`、`filterTypes` 和 `keys.types`；
- template 根据 type 生成筛选按钮和 `data-type`；
- `spawnPrivilegeStates()` 输出 privilege cell 的 type 信息；
- client-side filtering 从 column index 切换到 semantic type；
- copy privileges 逻辑从 slice index 切换到 type-based filter。

这说明失败不是因为模型完全没理解任务。它理解了大量正确结构，也确实进行了跨文件修改。

### 2. fuzzy edit 把“文本被替换”包装成了连续成功信号

legacy run 中 24 次 `edit` 调用都完成，并且都记录为 `editStrategy=fuzzy`。对于模型来说，这意味着每一步局部操作都被工具确认成功。

但 edit 成功只说明某段文本被替换了，不说明：

- 替换后的输出符合已有测试的字符串契约；
- 新增属性的顺序不会影响 snapshot/string comparison；
- helper、template、client JS 三层之间完全一致；
- 修改没有破坏已有 public behavior；
- 需要保留的兼容输出没有被无意改变。

在这个任务里，最终失败点非常具体：`spawnPrivilegeStates()` 输出的 `td` 属性顺序变成了 `data-privilege`, `data-type`, `data-value`，而测试期望 `data-privilege`, `data-value`, `data-type`。这看似是一个细节，但对于 NodeBB 的 template helper 测试来说是实际行为回归。

### 3. 模型把局部成功升级成“全局方案完成”

原始上下文里，模型在多次 fuzzy edit 成功后，把相关 TODO 标为完成：

- `Update client-side JS filtering logic, spawnPrivilegeStates, and copying logic`
- `Final verification: check all modified files are consistent and correct`

随后模型总结说 `spawnPrivilegeStates()` 已经正确接受第三个 `privTypes` 参数，并且会给 privilege cell 添加 `data-type`。

这一步是链路中最关键的放大：工具返回的是“局部替换完成”，模型推断成“这个子系统已经完成”，再进一步推断成“任务已经完成”。

### 4. evaluator 在模型最确信的区域失败

官方 evaluator 失败的测试正是：

```text
helpers should spawn privilege states
```

这和模型最终总结里强调的 `spawnPrivilegeStates()` 是同一个区域。也就是说，失败并不是发生在模型没碰到的角落，而是发生在模型认为自己已经完成并总结过的核心路径上。

### 5. exact 的通过说明严格失败信号有价值

`exact` 方案同样不是一路顺滑。它出现过 `OLD_TEXT_NOT_FOUND` 和 transaction conflict，但最终通过。这个对照说明：工具“少报错”不一定更好。严格工具会更早暴露模型假设与当前文件不匹配的地方，迫使模型重新读取、缩小编辑范围或重建计划。

legacy 的 fuzzy edit 则更像润滑剂：它减少了局部编辑失败，却也减少了模型停下来重新核对的机会。

## 因果链条

这个案例可以概括为：

```text
fuzzy edit 局部替换成功
  -> 模型认为当前子任务完成
  -> 模型继续在其他文件上叠加依赖该修改的改动
  -> TODO 和最终总结把该路径标成完成
  -> 官方测试在同一行为契约上失败
```

这里的“影响模型表现”不是说 edit 工具直接生成了错误代码，而是说工具反馈改变了模型的信念更新过程。模型基于工具反馈判断“我是否完成了这个子问题”。如果工具反馈过于宽松，模型可能在错误的状态上继续推理。

## 为什么这是工具相关，而不只是随机性

这个任务的证据强度比较高，原因有三点：

1. `legacy` primary failed。
2. `legacy` retry still failed。
3. `exact`、`anchor`、`lsp_symbol` primary passed，`patch` after rerun/fix passed。

如果只是模型随机性，legacy retry 至少有机会走出不同轨迹；但它仍然失败。而其他工具方案能通过，说明任务本身在当前模型能力范围内。

当然，这不能证明 fuzzy edit 在所有任务上都更差；它证明的是，在这个任务里，OpenCode 原生 fuzzy edit 的局部成功反馈与模型的计划推进方式发生了不利耦合。

## 对 agent harness 的启示

agent harness 中的 edit 工具不只是“文件修改 API”。它也是模型的反馈通道。

一个好的 edit 工具需要在两件事之间平衡：

- 足够易用，让模型不会因为无意义的 whitespace mismatch 卡住；
- 足够严格，让模型在关键跨文件修改中不会把“文本被替换”误认为“语义已经正确”。

NodeBB 这个案例说明，当任务需要多文件一致性时，过于宽容的 fuzzy edit 会让模型更容易错过细粒度行为契约。
