<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { NModal } from 'naive-ui'
import { ArrowRight, Check, X } from 'lucide-vue-next'
import { api, errorText, type Row } from '../api'

const props = defineProps<{ kind: string; record?: Row }>()
const emit = defineEmits<{ close: []; saved: [row: Row] }>()
const titles: Record<string, string> = {
  'paper-accounts': '创建模拟盘',
  backtests: '创建并启动回测',
  experiments: '创建对照实验',
  'strategy-versions': '创建策略版本',
  reports: '生成研究报告',
  improvements: '提交改进建议',
  review: '审核改进建议',
  evaluations: '登记策略评估',
  promote: '人工评审晋级',
  halt: '全局暂停',
  release: '解除全局暂停',
  cancel: '取消回测',
  trigger: '触发维护任务',
}
const title = titles[props.kind] || props.kind
const strategies = ref<Row[]>([]),
  accounts = ref<Row[]>([]),
  ready = ref(false)
const experiments = ref<Row[]>([]),
  backtests = ref<Row[]>([]),
  evaluations = ref<Row[]>([])
const extra = ref<Record<string, string>>({})
const checks = {
  quality: '数据质量',
  delay: '延迟压力',
  extreme: '极端行情',
  multiple_testing: '多重检验',
  independent_sample: '样本独立性',
  continuous_coverage: '持续运行覆盖',
}
const checked = ref<string[]>([])
const values = reactive({
  feeMultiplier: 1,
  latencyMultiplier: 1,
  minimumEdge: '0',
  experiment: '',
  baseRun: '',
  stressRun: '',
  cost: '',
  evaluation: '',
  name: '',
  amount: '1000',
  mode: 'PAPER',
  strategy: 'S00',
  version: '1.0.0',
  newVersion: '',
  model: 'conservative-v1',
  risk: '0.005',
  loss: '0.02',
  drawdown: '0.05',
  tail: 60,
  params: '{}',
  hypothesis: '',
  criteria: '',
  problem: '',
  evidence: '',
  reason: '',
  decision: 'APPROVED',
  start: '',
  end: '',
  account: '',
})
const assets = ref(['BTC', 'ETH']),
  periods = ref([5, 15]),
  selected = ref<string[]>([])
const reviewing = ref(false),
  busy = ref(false),
  error = ref(''),
  payload = ref<Row>({}),
  key = ref('')
const versions = computed(() =>
  strategies.value.filter((s) => s.strategy_id === values.strategy),
)
const usesStrategy = [
  'paper-accounts',
  'backtests',
  'strategy-versions',
  'improvements',
].includes(props.kind)
const usesDates = ['backtests', 'reports'].includes(props.kind)
const endpoint = computed(
  () =>
    ({
      promote:
        'strategy-versions/' +
        props.record?.strategy_id +
        '/' +
        props.record?.version +
        '/promote',
      review: 'improvements/' + props.record?.id + '/review',
      cancel: 'backtests/' + props.record?.id + '/cancel',
      halt: 'risk/halt',
      release: 'risk/release',
      trigger:
        'jobs/' + encodeURIComponent(String(props.record?.name)) + '/trigger',
    })[props.kind] || props.kind,
)
onMounted(async () => {
  try {
    if (usesStrategy)
      strategies.value = (await api<Row[]>('strategy-versions?limit=200')).data
    if (usesStrategy && versions.value.length)
      values.version = String(versions.value[0].version)
    if (['experiments', 'reports', 'evaluations'].includes(props.kind))
      accounts.value = (await api<Row[]>('paper-accounts?limit=200')).data
    if (props.kind === 'evaluations') {
      const [ex, bt] = await Promise.all([
        api<Row[]>('experiments?limit=200'),
        api<Row[]>('backtests?status=COMPLETED&limit=200'),
      ])
      experiments.value = ex.data
      backtests.value = bt.data
    }
    if (props.kind === 'promote') {
      evaluations.value = (
        await api<Row[]>(
          'evaluations?limit=200&strategy_id=' +
            encodeURIComponent(String(props.record?.strategy_id)),
        )
      ).data.filter(
        (row) =>
          row.strategy_version === props.record?.version &&
          row.status === 'VALIDATION_PASSED',
      )
    }
    ready.value = true
  } catch (e) {
    error.value = errorText(e)
  }
})
watch(
  () => values.strategy,
  () => {
    values.version = String(versions.value[0]?.version ?? '')
  },
)
function jsonObject() {
  const obj: unknown = JSON.parse(values.params)
  if (!obj || typeof obj !== 'object' || Array.isArray(obj))
    throw Error('参数必须是 JSON 对象。')
  return obj
}
function dates() {
  const start = Date.parse(values.start + ':00Z'),
    end = Date.parse(values.end + ':00Z')
  if (!Number.isFinite(start) || !Number.isFinite(end) || start >= end)
    throw Error('UTC 截止时间必须晚于起始时间。')
  return {
    start_time: new Date(start).toISOString(),
    end_time: new Date(end).toISOString(),
  }
}
function prepare() {
  try {
    error.value = ''
    const config = {
      assets: assets.value,
      timeframes: periods.value,
      simulation_model: values.model,
      risk_per_trade: values.risk,
      daily_loss: values.loss,
      max_drawdown: values.drawdown,
      tail_seconds: values.tail,
      cost_multiplier: values.feeMultiplier,
      latency_multiplier: values.latencyMultiplier,
      minimum_edge: values.minimumEdge,
      strategies: Object.entries(extra.value)
        .filter(([id, v]) => id !== values.strategy && v)
        .map(([strategy_id, strategy_version]) => ({
          strategy_id,
          strategy_version,
        })),
    }
    const strategy = {
      strategy_id: values.strategy,
      strategy_version: values.version,
      initial_balance: values.amount,
      config,
    }
    if (
      ['paper-accounts', 'backtests'].includes(props.kind) &&
      (!assets.value.length || !periods.value.length || !values.version)
    )
      throw Error('请选择市场范围与已注册的策略版本。')
    switch (props.kind) {
      case 'paper-accounts':
        payload.value = {
          ...strategy,
          name: values.name.trim(),
          mode: values.mode,
        }
        break
      case 'backtests':
        payload.value = { ...strategy, ...dates() }
        break
      case 'experiments':
        if (selected.value.length < 2)
          throw Error('对照实验需要至少两个独立账户。')
        payload.value = {
          name: values.name,
          hypothesis: values.hypothesis,
          criteria: values.criteria,
          account_ids: selected.value,
        }
        break
      case 'strategy-versions':
        payload.value = {
          strategy_id: values.strategy,
          version: values.newVersion,
          config: jsonObject(),
          base_version: values.version,
          description: values.reason,
        }
        break
      case 'improvements':
        payload.value = {
          strategy_id: values.strategy,
          problem: values.problem,
          evidence: values.evidence,
          hypothesis: values.hypothesis,
          acceptance: values.criteria,
        }
        break
      case 'evaluations':
        if (values.baseRun === values.stressRun)
          throw Error('请选择两次独立回放。')
        payload.value = {
          experiment_id: values.experiment,
          account_id: values.account,
          backtest_id: values.baseRun,
          stress_backtest_id: values.stressRun,
          cost_usd: values.cost,
          checks: Object.fromEntries(
            Object.keys(checks).map((k) => [k, checked.value.includes(k)]),
          ),
          evidence: values.evidence,
        }
        break
      case 'promote':
        payload.value = {
          evaluation_id: values.evaluation,
          reason: values.reason,
        }
        break
      case 'reports':
        payload.value = {
          ...dates(),
          ...(values.account ? { account_id: values.account } : {}),
        }
        break
      case 'release':
        payload.value = { reason: values.reason }
        break
      case 'review':
        payload.value = { status: values.decision, reason: values.reason }
        break
      default:
        payload.value = {}
    }
    key.value = crypto.randomUUID()
    reviewing.value = true
  } catch (e) {
    error.value = errorText(e)
  }
}
async function commit() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    emit(
      'saved',
      (await api<Row>(endpoint.value, { body: payload.value, key: key.value }))
        .data,
    )
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <NModal
    :show="true"
    :mask-closable="!busy"
    @update:show="
      (v) => {
        if (!v && !busy) emit('close')
      }
    "
    ><section class="dialog" role="dialog" :aria-label="title">
      <div class="panel-head">
        <div>
          <small class="kicker">RESEARCH CONSOLE</small>
          <h2>{{ reviewing ? '确认操作' : title }}</h2>
        </div>
        <button
          class="icon"
          :disabled="busy"
          aria-label="关闭表单"
          @click="emit('close')"
        >
          <X :size="18" />
        </button>
      </div>
      <form v-if="!reviewing" @submit.prevent="prepare" class="edit-form">
        <template v-if="kind === 'promote'">
          <p v-if="!evaluations.length" class="muted">
            此策略版本暂无符合晋级条件的评估记录。
          </p>
          <label
            >已通过的评估<select v-model="values.evaluation" required>
              <option
                v-for="e in evaluations"
                :key="String(e.id)"
                :value="String(e.id)"
              >
                {{ e.id }}
              </option>
            </select></label
          >
          <label
            >人工晋级理由<textarea v-model="values.reason" required />
          </label>
        </template>
        <template v-if="kind === 'evaluations'">
          <label
            >预注册实验<select v-model="values.experiment" required>
              <option
                v-for="e in experiments"
                :key="String(e.id)"
                :value="String(e.id)"
              >
                {{ e.name }}
              </option>
            </select></label
          >
          <label
            >受评模拟账户<select v-model="values.account" required>
              <option
                v-for="a in accounts.filter((a) =>
                  (
                    experiments.find((e) => e.id === values.experiment)
                      ?.account_ids as string[] | undefined
                  )?.includes(String(a.id)),
                )"
                :key="String(a.id)"
                :value="String(a.id)"
              >
                {{ a.name }}
              </option>
            </select></label
          >
          <label
            >样本外基准回放<select v-model="values.baseRun" required>
              <option
                v-for="b in backtests"
                :key="String(b.id)"
                :value="String(b.id)"
              >
                {{ b.id }}
              </option>
            </select></label
          >
          <label
            >同期压力回放<select v-model="values.stressRun" required>
              <option
                v-for="b in backtests.filter((b) => b.id !== values.baseRun)"
                :key="String(b.id)"
                :value="String(b.id)"
              >
                {{ b.id }}
              </option>
            </select></label
          >
          <label
            >研究运行成本（USD）<input
              v-model="values.cost"
              type="number"
              required
              min="0"
              step=".00000001"
          /></label>
          <fieldset>
            <legend>人工核验通过项</legend>
            <label v-for="(name, k) in checks" :key="k" class="check"
              ><input v-model="checked" type="checkbox" :value="k" />{{
                name
              }}</label
            >
          </fieldset>
          <label
            >证据与核验依据<textarea v-model="values.evidence" required />
          </label>
          <p v-if="!experiments.length || backtests.length < 2" class="muted">
            需要预注册实验和两份已完成的回放报告。
          </p>
        </template>
        <label v-if="['paper-accounts', 'experiments'].includes(kind)"
          >名称<input
            v-model="values.name"
            required
            maxlength="120"
            placeholder="自定义研究名称"
        /></label>
        <div v-if="kind === 'paper-accounts'" class="form-grid">
          <label
            >运行模式<select v-model="values.mode">
              <option>PAPER</option>
              <option>SHADOW</option>
            </select></label
          ><label
            >初始虚拟资金（USD）<input
              v-model="values.amount"
              type="number"
              min=".00000001"
              step=".00000001"
              required
          /></label>
        </div>
        <label v-if="usesStrategy"
          >研究策略<select v-model="values.strategy" required>
            <option
              v-for="s in [
                ...new Set(strategies.map((v) => String(v.strategy_id))),
              ]"
              :key="s"
            >
              {{ s }}
            </option>
          </select></label
        >
        <template v-if="['paper-accounts', 'backtests'].includes(kind)">
          <div class="form-grid">
            <label
              >策略版本<select v-model="values.version" required>
                <option v-for="v in versions" :key="String(v.version)">
                  {{ v.version }}
                </option>
              </select></label
            ><label
              >撮合模型<select v-model="values.model">
                <option value="basic-v1">基础 v1</option>
                <option value="conservative-v1">保守 v1</option>
                <option value="stress-v1">压力 v1</option>
              </select></label
            >
          </div>
          <div class="form-grid">
            <fieldset>
              <legend>资产</legend>
              <label v-for="a in ['BTC', 'ETH']" :key="a" class="check"
                ><input v-model="assets" type="checkbox" :value="a" />{{
                  a
                }}</label
              >
            </fieldset>
            <fieldset>
              <legend>市场周期</legend>
              <label v-for="p in [5, 15]" :key="p" class="check"
                ><input v-model="periods" type="checkbox" :value="p" />{{
                  p
                }}m</label
              >
            </fieldset>
          </div>
          <details>
            <summary>风险与实验参数</summary>
            <div class="form-grid subform">
              <label
                >单笔风险上限（比例）<input
                  v-model="values.risk"
                  type="number"
                  min=".000001"
                  max=".005"
                  step=".000001"
                  required /></label
              ><label
                >单日亏损上限（比例）<input
                  v-model="values.loss"
                  type="number"
                  min=".000001"
                  max=".02"
                  step=".000001"
                  required /></label
              ><label
                >最大回撤（比例）<input
                  v-model="values.drawdown"
                  type="number"
                  min=".000001"
                  max=".05"
                  step=".000001"
                  required /></label
              ><label
                >尾盘研究窗口（秒）<input
                  v-model.number="values.tail"
                  type="number"
                  min="1"
                  max="900"
                  required
              /></label>
            </div>
            <div class="form-grid">
              <label
                >费用压力倍数<input
                  v-model.number="values.feeMultiplier"
                  type="number"
                  min="1"
                  max="3"
                  step=".01"
                  required /></label
              ><label
                >延迟压力倍数<input
                  v-model.number="values.latencyMultiplier"
                  type="number"
                  min="1"
                  max="5"
                  step=".01"
                  required /></label
              ><label
                >最小净价差<input
                  v-model="values.minimumEdge"
                  type="number"
                  min="0"
                  max="1"
                  step=".000001"
                  required
              /></label>
            </div>
            <p class="muted">
              基础、保守、压力模型分别使用 0、1、2 个 tick
              的滑点，盘口可用深度分别为
              75%、50%、25%。费用以市场费率版本为基准。
            </p>
            <fieldset
              v-if="strategies.some((s) => s.strategy_id !== values.strategy)"
            >
              <legend>附加组合策略</legend>
              <label
                v-for="id in [
                  ...new Set(strategies.map((s) => String(s.strategy_id))),
                ].filter((id) => id !== values.strategy)"
                :key="id"
                >{{ id
                }}<select v-model="extra[id]">
                  <option value="">不启用</option>
                  <option
                    v-for="v in strategies.filter((s) => s.strategy_id === id)"
                    :key="String(v.version)"
                  >
                    {{ v.version }}
                  </option>
                </select></label
              >
            </fieldset>
          </details>
        </template>
        <template v-if="usesDates"
          ><div class="form-grid">
            <label
              >起始时间（UTC）<input
                v-model="values.start"
                type="datetime-local"
                required /></label
            ><label
              >截止时间（UTC）<input
                v-model="values.end"
                type="datetime-local"
                required
            /></label></div
        ></template>
        <label v-if="kind === 'reports'"
          >模拟账户<select v-model="values.account">
            <option value="">全部账户</option>
            <option
              v-for="a in accounts"
              :key="String(a.id)"
              :value="String(a.id)"
            >
              {{ a.name }}
            </option>
          </select></label
        >
        <template v-if="kind === 'experiments'"
          ><fieldset>
            <legend>对照账户</legend>
            <label v-for="a in accounts" :key="String(a.id)" class="check"
              ><input
                v-model="selected"
                type="checkbox"
                :value="String(a.id)"
              />{{ a.name }}</label
            >
            <p v-if="!accounts.length" class="muted">请先创建独立模拟账户。</p>
          </fieldset></template
        >
        <template v-if="kind === 'improvements'"
          ><label>研究问题<textarea v-model="values.problem" required /></label
          ><label>统计证据<textarea v-model="values.evidence" required /></label
        ></template>
        <template v-if="['experiments', 'improvements'].includes(kind)"
          ><label
            >研究假设<textarea v-model="values.hypothesis" required /></label
          ><label>验收标准<textarea v-model="values.criteria" required /></label
        ></template>
        <template v-if="kind === 'strategy-versions'"
          ><label
            >基础版本<select v-model="values.version" required>
              <option v-for="v in versions" :key="String(v.version)">
                {{ v.version }}
              </option>
            </select></label
          ><label
            >新版本号<input
              v-model="values.newVersion"
              placeholder="1.1.0"
              required /></label
          ><label
            >策略参数（JSON）<textarea
              v-model="values.params"
              required
            /></label
          ><label>变更说明<textarea v-model="values.reason" required /></label
        ></template>
        <template v-if="kind === 'review'"
          ><label
            >审核结论<select v-model="values.decision">
              <option value="APPROVED">批准</option>
              <option value="REJECTED">驳回</option>
            </select></label
          ><label>依据<textarea v-model="values.reason" required /></label
        ></template>
        <label v-if="kind === 'release'"
          >人工恢复依据<textarea v-model="values.reason" required />
        </label>
        <p v-if="kind === 'halt'">
          将暂停本系统的模拟交易，请核对所有运行中的研究实例。
        </p>
        <p v-if="kind === 'cancel'">取消此回测任务后，将保留其运行记录。</p>
        <p v-if="error" role="alert" class="error">{{ error }}</p>
        <div class="form-actions">
          <button type="button" class="btn" @click="emit('close')">取消</button
          ><button class="primary" :disabled="!ready">
            核对配置<ArrowRight :size="15" />
          </button>
        </div>
      </form>
      <div v-else class="edit-form">
        <p>确认执行「{{ title }}」？</p>
        <pre v-if="Object.keys(payload).length" class="json">{{
          JSON.stringify(payload, null, 2)
        }}</pre>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <div class="form-actions">
          <button class="btn" :disabled="busy" @click="reviewing = false">
            返回修改</button
          ><button class="primary" :disabled="busy" @click="commit">
            <Check :size="15" />{{ busy ? '正在提交…' : '确认提交' }}
          </button>
        </div>
      </div>
    </section></NModal
  >
</template>
