# ProtonMail Webclients Recurrence：legacy 的随机性反例与 first-pass reliability

## 结论摘要

Webclients 这个任务不是 legacy 永远做不出来的例子。相反，它是一个非常重要的反例：legacy primary run 失败，但 targeted retry 通过。这说明模型随机性确实存在，不能把所有 legacy 失败都归因于工具。

但它仍然支持“edit 工具会影响模型表现”的结论。原因是：同一任务中，`exact`、`patch`、`lsp_symbol` 在 primary run 就通过，`anchor` 的最终结果也是 pass，而 legacy primary run 走出了一条看似完成、但破坏大量 public module path 的轨迹。legacy retry 能通过，说明模型能力足够；primary 失败说明 legacy 工具面没有稳定地把模型引导到正确轨迹。

这个案例的价值在于限定结论：我们不是说 OpenCode 原生 fuzzy edit 必然失败，而是说它降低了 first-pass reliability，尤其容易让模型在“文件存在、类型检查通过、barrel export 建好了”的局部成功下，忽略测试实际依赖的 deep import path。

## 任务与结果

任务：`instance_protonmail__webclients-caf10...`

这个任务涉及 calendar recurrence / alarms / mail integration 模块拆分和导出路径整理。测试覆盖多个 direct import path，例如：

- `../../lib/calendar/alarms/trigger`
- `../../lib/calendar/recurrence/getFrequencyString`
- `../../../lib/calendar/mailIntegration/invite`
- `../../../lib/calendar/recurrence/rrule`
- `../../../lib/calendar/recurrence/rruleEqual`

结果对照：

| arm | result |
| --- | --- |
| `legacy` | fail |
| `exact` | pass |
| `patch` | pass |
| `lsp_symbol` | pass |
| `anchor` | pass |
| `legacy` targeted retry | pass |

关键 metrics：

| run | tokens | tool calls | edit-related calls | result |
| --- | ---: | ---: | --- | --- |
| `legacy` primary | 2.84M | 100 | 18 `edit`, 17 fuzzy-completed; 7 `write` | fail |
| `legacy` retry | 3.06M | 88 | 15 `edit`; 18 `write` | pass |

## 原始行为链路

下面是从 primary legacy run 压缩出的行为 transcript。

原始上下文证据文件：[raw-context/webclients-legacy-context-excerpt.md](raw-context/webclients-legacy-context-excerpt.md)

```text
[legacy edit tool: /app/packages/shared/lib/calendar/rrule.ts]
Edit applied successfully. (editStrategy=fuzzy)

[model]
First, let me add the `getPositiveSetpos` and `getNegativeSetpos` functions to `rrule.ts`:

[legacy edit tool: /app/packages/shared/lib/calendar/rrule.ts]
Edit applied successfully. (editStrategy=fuzzy)

[bash]
mkdir -p /app/packages/shared/lib/calendar/recurrence /app/packages/shared/lib/calendar/alarms /app/packages/shared/lib/calendar/mailIntegration ...
(no output)

[legacy edit tool: /app/packages/shared/lib/date/timezone.ts]
Edit applied successfully. (editStrategy=fuzzy)

[legacy edit tool: /app/packages/shared/lib/calendar/crypto/helpers/index.ts]
Edit applied successfully. (editStrategy=fuzzy)

[local type check]
npx tsc --noEmit --pretty 2>&1 | head -80
(no output)

[legacy edit tool: /app/applications/calendar/src/app/containers/calendar/InteractiveCalendarView.tsx]
Edit applied successfully. (editStrategy=fuzzy)

[legacy edit tool: /app/applications/calendar/src/app/containers/calendar/eventActions/getSaveEventActions.ts]
Edit applied successfully. (editStrategy=fuzzy)

[todo state]
completed Create calendar/recurrence/index.ts barrel file
completed Create calendar/alarms/index.ts barrel file
completed Create calendar/mailIntegration/index.ts barrel file
completed Update imports in InteractiveCalendarView.tsx and eventActions/*
completed Verify with type checking

[model]
All changes are complete. Here's a summary of what was implemented:
- `calendar/recurrence/index.ts` re-exports `rrule`, `rruleEqual`, `rruleUntil`, `rruleWkst`, `recurring`, ...
- `calendar/alarms/index.ts` re-exports `getValarmTrigger`, `trigger`, ...
- `calendar/mailIntegration/index.ts` re-exports all invitation-related helpers from `integration/invite`
- updated application imports to resolve from `calendar/recurrence`, `calendar/mailIntegration`

[official evaluator]
Module not found: Can't resolve '../../lib/calendar/alarms/trigger'
Module not found: Can't resolve '../../lib/calendar/recurrence/getFrequencyString'
Module not found: Can't resolve '../../../lib/calendar/mailIntegration/invite'
Module not found: Can't resolve '../../../lib/calendar/recurrence/rrule'
...

legacy primary resolved=false
legacy retry resolved=true
exact primary resolved=true
patch primary resolved=true
lsp_symbol primary resolved=true
anchor resolved=true
```

## 逐步分析

### 1. 模型选择了 barrel export 路线

模型创建了：

- `calendar/recurrence/index.ts`
- `calendar/alarms/index.ts`
- `calendar/mailIntegration/index.ts`
- `calendar/crypto/decrypt/index.ts`
- `calendar/crypto/helpers/index.ts`

并把应用代码 import 改成从这些 barrel module 读取。这个方向对“整理导出”类任务来说很自然，也不一定错误。

问题在于，官方测试和已有代码仍然依赖 deep module paths，例如：

```text
../../lib/calendar/alarms/trigger
../../../lib/calendar/recurrence/rrule
../../../lib/calendar/mailIntegration/invite
```

只创建 barrel `index.ts` 并不等于保留这些 deep import 文件。模型解决了“新的聚合入口”，但破坏了“旧的直接入口”。

### 2. local type check 给了过强的安全感

legacy primary run 中，模型运行：

```text
npx tsc --noEmit --pretty 2>&1 | head -80
(no output)
```

这看起来像一个很强的成功信号。但它没有覆盖 evaluator 运行的 webpack test import resolution。也就是说，type check 没有报错，不代表所有 test entry points 的 module path 都可解析。

模型随后把 `Verify with type checking` 标成 completed，并总结“所有变更完成”。

### 3. fuzzy edit 让大规模 import rewrite 看起来稳定

legacy primary run 中，模型对许多 application event action 文件调用 fuzzy edit，并得到连续成功：

- `InteractiveCalendarView.tsx`
- `dtstamp.ts`
- `getSaveSingleEventActions.ts`
- `getDeleteRecurringEventActions.ts`
- `getDeleteEventActions.ts`
- `getRecurringSaveType.ts`
- `getRecurringDeleteType.ts`
- `getSaveRecurringEventActions.ts`
- `recurringHelper.ts`
- `inviteActions.ts`
- `getSaveEventActions.ts`
- `getRecurringUpdateAllPossibilities.ts`

这些成功说明“应用代码里的 import 被改了”。但 evaluator 失败的是 tests 仍然从原 deep path 导入。fuzzy edit 让模型顺利完成了它选定的迁移路线，却没有强迫它检查未迁移的外部入口。

### 4. evaluator 失败说明 public path 被破坏

官方 evaluator 报出多处 `Module not found`，覆盖 recurrence、alarms、mailIntegration、rrule 等多个路径。这不是某一个文件的 typo，而是模型整体策略的问题：它把模块组织成 barrel exports，但没有保留兼容 deep import 的文件结构。

### 5. retry 通过说明这是 reliability 问题，而不是能力上限

同样的 legacy 方案 targeted retry 通过，`anchor` 的最终结果也是 pass。这说明模型并非不能解决该任务。更合理的解释是：

- primary run 采样到了一条“barrel export + import rewrite”的轨迹；
- 这条轨迹在 local type check 下看似完成；
- 但没有覆盖测试所依赖的 public module path；
- retry 采样到另一条更兼容 evaluator 的轨迹。

因此，Webclients 不是“legacy 必败”的证据，而是“legacy first-pass reliability 较差”的证据。

## 因果链条

```text
fuzzy edit 成功修改多个 import 和 helper 文件
  -> 模型选择 barrel export 作为主要解决方案
  -> local tsc 无输出
  -> 模型把 Verify with type checking 标成完成
  -> 模型总结任务完成
  -> 官方 webpack tests 仍从 deep import path 解析模块
  -> 多个 Module not found
```

这个链条和 NodeBB / OpenLibrary 的共同点是：模型把局部成功信号当成全局正确信号。区别是，Webclients 的局部成功来自“type check 无输出 + fuzzy edits 完成”，而最终失败来自 test bundler 的 module resolution。

## 对 agent harness 的启示

在大型 TypeScript monorepo 中，edit 工具和验证工具一起决定模型的轨迹稳定性。

如果 edit 工具很宽松、验证又只覆盖 `tsc`，模型可能会：

- 大规模重写 import；
- 创建 barrel files；
- 让当前 application code type-check；
- 忽略 tests 和外部代码仍然依赖 deep import path。

更严格的 edit 工具不一定直接防止这种策略错误，但它会让每次替换更显式、更可审计，也更容易在局部不匹配时迫使模型重新查看具体文件。

Webclients 案例提醒我们：工具影响不是绝对的。legacy 可以在 retry 中通过，但 primary run 的失败说明，工具面会改变 first-pass 轨迹的稳定性。
