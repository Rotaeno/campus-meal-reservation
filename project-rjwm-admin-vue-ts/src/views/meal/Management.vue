<template>
  <div class="meal-board">
    <header><div><h2>校园午餐 · 预订与核销</h2><p>按餐次接收预订，按份数备餐，凭取餐码完成核销。</p></div><el-button @click="openCustomer">打开学生预订页</el-button></header>
    <el-card class="section">
      <div slot="header">发布餐次 <small>时间均为北京时间；关闭餐次仅停止新预订。</small></div>
      <el-form inline size="small">
        <el-form-item label="餐次"><el-input v-model="form.title" maxlength="64" /></el-form-item>
        <el-form-item label="可售份数"><el-input-number v-model="form.capacity" :min="1" :max="5000" /></el-form-item>
        <el-form-item label="截单时间"><el-date-picker v-model="form.cutoffTime" type="datetime" value-format="yyyy-MM-dd HH:mm:ss" /></el-form-item>
        <el-form-item label="自提开始"><el-date-picker v-model="form.pickupStart" type="datetime" value-format="yyyy-MM-dd HH:mm:ss" /></el-form-item>
        <el-form-item label="自提结束"><el-date-picker v-model="form.pickupEnd" type="datetime" value-format="yyyy-MM-dd HH:mm:ss" /></el-form-item>
        <el-button type="primary" :loading="busy" @click="createSession">发布</el-button>
      </el-form>
    </el-card>
    <el-card class="section">
      <div slot="header">餐次列表 <el-button class="right" size="mini" @click="refresh">刷新</el-button></div>
      <el-table :data="sessions" @row-click="selectSession" highlight-current-row>
        <el-table-column prop="title" label="餐次" /><el-table-column prop="mealDate" label="日期" width="120" />
        <el-table-column label="名额" width="130"><template slot-scope="s">{{ s.row.reservedCount }} / {{ s.row.capacity }} 份</template></el-table-column>
        <el-table-column prop="cutoffTime" label="截单时间" width="180" />
        <el-table-column label="自提时段"><template slot-scope="s">{{ s.row.pickupStart }} ～ {{ s.row.pickupEnd }}</template></el-table-column>
        <el-table-column label="操作" width="180"><template slot-scope="s"><el-button size="mini" @click.stop="selectSession(s.row)">订单/备餐</el-button><el-button size="mini" @click.stop="toggle(s.row)">{{ s.row.status === 1 ? '关闭' : '开放' }}</el-button></template></el-table-column>
      </el-table>
    </el-card>
    <el-card v-if="selected" class="section">
      <div slot="header">{{ selected.title }} · 订单与备餐</div>
      <p class="preparation">待备餐：<span v-for="item in preparation" :key="item.name">{{ item.name }} × {{ item.quantity }}　</span><span v-if="!preparation.length">暂无已支付待备餐订单</span></p>
      <el-table :data="orders"><el-table-column prop="number" label="订单号" min-width="220" /><el-table-column prop="consignee" label="取餐人" width="120" /><el-table-column prop="reservedQuantity" label="份数" width="70" /><el-table-column prop="amount" label="金额" width="80" />
        <el-table-column label="状态" width="160"><template slot-scope="s">{{ state(s.row) }}</template></el-table-column>
        <el-table-column label="操作" width="230"><template slot-scope="s"><el-button v-if="s.row.status === 2" size="mini" @click="accept(s.row)">接单</el-button><el-button v-if="s.row.status === 3" size="mini" @click="ready(s.row)">备餐就绪</el-button><el-button v-if="s.row.status !== 5 && s.row.status !== 6" size="mini" @click="cancel(s.row)">取消/退款</el-button></template></el-table-column>
      </el-table>
      <div class="redeem"><el-input v-model="pickupCode" placeholder="输入学生出示的22位取餐码" maxlength="22" /><el-button type="primary" @click="redeem">核销取餐</el-button></div>
    </el-card>
    <el-card class="section"><div slot="header">退款任务 <small>本地处理失败不代表资金未退；人工重试先查询原退款结果。</small></div>
      <el-table :data="refunds"><el-table-column prop="orderId" label="订单ID" width="90" /><el-table-column label="处理状态" width="120"><template slot-scope="s">{{ taskState(s.row.status) }}</template></el-table-column><el-table-column label="平台最后结果" width="130"><template slot-scope="s">{{ providerState(s.row.providerState) }}</template></el-table-column><el-table-column prop="attempts" label="处理次数" width="90" /><el-table-column prop="failures" label="失败次数" width="90" /><el-table-column prop="lastError" label="诊断" min-width="170" /><el-table-column prop="nextRetryTime" label="下次处理" min-width="160" /><el-table-column label="操作" width="180"><template slot-scope="s"><el-button v-if="s.row.status === 'FAILED'" size="mini" @click="retry(s.row)">重试</el-button><el-button size="mini" @click="history(s.row)">记录</el-button></template></el-table-column></el-table>
    </el-card>
    <el-dialog title="人工恢复记录" :visible.sync="reviewVisible" width="680px"><el-table :data="reviews"><el-table-column prop="operatorId" label="操作人ID" width="100" /><el-table-column prop="reason" label="原因" /><el-table-column prop="previousProviderState" label="重试前平台结果" width="150" /><el-table-column prop="createTime" label="时间" width="170" /></el-table></el-dialog>
  </div>
</template>
<script lang="ts">
import Vue from 'vue'
import request from '@/utils/request'
interface Session { id: string; title: string; status: number; [key: string]: any }
interface MealOrder { id: string; status: number; payStatus: number; refundStatus: string; [key: string]: any }
function stamp(date: Date): string {
  const part = (n: number) => String(n).padStart(2, '0')
  return date.getFullYear() + '-' + part(date.getMonth() + 1) + '-' + part(date.getDate()) + ' ' + part(date.getHours()) + ':' + part(date.getMinutes()) + ':00'
}
export default Vue.extend({
  data() { const date = new Date(); date.setDate(date.getDate() + 1); date.setHours(10,30,0,0); return {
    form: { title: '校园午餐', capacity: 50, cutoffTime: stamp(date), pickupStart: stamp(new Date(date.getTime()+3600000)), pickupEnd: stamp(new Date(date.getTime()+9000000)) },
    sessions: [] as Session[], selected: null as Session | null, orders: [] as MealOrder[], preparation: [] as any[], refunds: [] as any[], reviews: [] as any[], reviewVisible: false, pickupCode: '', busy: false, timer: 0
  } },
  mounted() { this.refresh(); this.timer = window.setInterval(() => this.refresh(), 10000) },
  beforeDestroy() { window.clearInterval(this.timer) },
  methods: {
    async api(method: string, url: string, data?: any) { const response: any = await request({ method: method as any, url, data }); if (response.data.code !== 1) { throw new Error(response.data.msg) }; return response.data.data },
    async action(work: () => Promise<any>) { try { await work(); this.$message.success('操作成功'); await this.refresh() } catch (e) { this.$message.error(e.message || '操作失败') } },
    openCustomer() { window.open('/#/campus', '_blank') },
    async createSession() { this.busy = true; await this.action(() => this.api('post','/meal/sessions', Object.assign({},this.form,{mealDate:this.form.pickupStart.slice(0,10)}))); this.busy = false },
    async refresh() { try { this.sessions = await this.api('get','/meal/sessions'); this.refunds = await this.api('get','/refunds'); if (this.selected) { await this.selectSession(this.selected) } } catch (e) { this.$message.error(e.message || '加载失败') } },
    async selectSession(session: Session) { this.selected = session; this.orders = await this.api('get','/meal/sessions/'+session.id+'/orders'); this.preparation = await this.api('get','/meal/sessions/'+session.id+'/preparation') },
    async toggle(session: Session) { await this.action(() => this.api('put','/meal/sessions/'+session.id+'/status/'+(session.status===1?0:1))) },
    async accept(order: MealOrder) { await this.action(() => this.api('put','/order/confirm',{id:order.id})) },
    async ready(order: MealOrder) { await this.action(() => this.api('put','/meal/orders/'+order.id+'/ready')) },
    async cancel(order: MealOrder) { await this.action(() => this.api('put','/order/cancel',{id:order.id,cancelReason:'餐次运营取消'})) },
    async redeem() { await this.action(() => this.api('post','/meal/redeem',{pickupCode:this.pickupCode})); this.pickupCode = '' },
    async retry(task: any) { try { const answer: any = await this.$prompt('填写核实情况或重试原因（200字以内）。重试沿用原退款号，先查询平台结果。','人工恢复',{inputValidator:(v: string) => !!v && !!v.trim() && v.length <= 200, inputErrorMessage:'请填写200字以内的原因'}); await this.action(() => this.api('post','/refunds/'+task.id+'/retry',{reason:answer.value})) } catch (e) { if (e !== 'cancel' && e !== 'close') { this.$message.error(e.message || '操作失败') } } },
    async history(task: any) { try { this.reviews = await this.api('get','/refunds/'+task.id+'/reviews'); this.reviewVisible = true } catch (e) { this.$message.error(e.message || '加载失败') } },
    taskState(value: string) { const names: {[key: string]: string} = {PENDING:'待处理',PROCESSING:'执行中',SUCCEEDED:'已确认',FAILED:'待人工处理'}; return names[value] || value },
    providerState(value: string) { const names: {[key: string]: string} = {UNKNOWN:'结果未知',PROCESSING:'处理中',SUCCEEDED:'已成功',CLOSED:'已关闭',ABNORMAL:'异常',NOT_FOUND:'未查到'}; return names[value] || '结果未知' },
    state(order: MealOrder) { const names: {[key: number]: string} = {1:'待支付',2:'待接单',3:'备餐中',4:'待自提',5:'已核销',6:'已取消'}; return names[order.status] + (order.refundStatus !== 'NONE' ? ' · 退款'+order.refundStatus : '') }
  }
})
</script>
<style scoped>
.meal-board{padding:24px;background:#f5f7fa;min-height:100vh}.meal-board header{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}h2{margin:0 0 8px;color:#183340}p,small{color:#657780}small{margin-left:14px}.section{margin-bottom:20px}.right{float:right}.preparation{padding:16px;background:#eef7f2;border-radius:8px}.redeem{display:flex;gap:12px;margin-top:20px;max-width:600px}
</style>
