import {z} from 'zod'
import {db,ApiError} from './db.js'
export const resources:Record<string,string>={
 'markets':'markets','market-events':'engine_events','orderbooks':'orderbook_snapshots','trades':'trade_ticks','reference-prices':'reference_ticks','data-quality':'data_quality_events','datasets':'dataset_versions','data-sources':'data_source_status',
 'paper-accounts':'paper_accounts','paper-sessions':'paper_sessions','paper-orders':'paper_orders','paper-fills':'paper_fills','paper-positions':'paper_positions','settlements':'paper_settlements','ledger':'balance_ledger','equity':'equity_snapshots','opportunities':'strategy_signals',
 evaluations:'strategy_evaluations',promotions:'version_promotions',strategies:'strategy_definitions','strategy-versions':'strategy_versions',experiments:'comparison_experiments','experiment-runs':'research_experiments','backtest-datasets':'backtest_datasets',backtests:'backtest_runs',risk:'risk_events',attributions:'attribution_evidence',reports:'performance_reports',analytics:'account_analytics',improvements:'improvement_proposals',logs:'audit_logs',system:'system_events',jobs:'scheduled_jobs',settings:'system_settings',notifications:'notification_logs'
}
const paging=z.object({limit:z.coerce.number().int().min(1).max(200).default(50),offset:z.coerce.number().int().min(0).max(10000000).default(0),direction:z.enum(['asc','desc']).default('desc')})
const schemaCache=new Map<string,Set<string>>()
const filtered=['account_id','market_id','asset','timeframe','mode','status','kind','data_quality','strategy_id','reason','side','source','fill_id','event_id','order_id']
export async function list(resource:string,query:Record<string,string>){
  if(!Object.hasOwn(resources,resource))throw new ApiError(404,'NOT_FOUND','资源不存在')
  const table=resources[resource], {limit,offset,direction}=paging.parse(query)
  let columns=schemaCache.get(table)
  if(!columns){
    const result=await db.query('SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=$1',[table])
    columns=new Set<string>(result.rows.map(r=>r.column_name))
    if(!columns.size)throw new ApiError(503,'SCHEMA','数据迁移尚未完成')
    schemaCache.set(table,columns)
  }
  const args:unknown[]=[],where:string[]=[]
  for(const key of filtered){
    if(!query[key])continue
    if(!columns.has(key))throw new ApiError(400,'FILTER','该资源不支持此过滤字段')
    args.push(query[key]);where.push(key+'=$'+args.length)
  }
  if (resource === 'attributions') {
    if (query.min_loss || query.max_loss) where.push('net_pnl<0')
    for (const [field,comparison] of [['min_loss','>='],['max_loss','<=']]) {
      if (!query[field]) continue
      args.push(z.string().regex(/^(?:0|[1-9]\d{0,12})(?:\.\d{1,8})?$/).parse(query[field]))
      where.push('(-net_pnl)'+comparison+'$'+args.length+'::numeric')
    }
    for(const [field,comparison] of [['start_time','>='],['end_time','<']]) {
      if (!query[field]) continue
      args.push(z.iso.datetime({offset:true}).parse(query[field]))
      where.push('created_at'+comparison+'$'+args.length+'::timestamptz')
    }
    if(query.start_time && query.end_time && Date.parse(query.start_time)>=Date.parse(query.end_time)) {
      throw new ApiError(400,'FILTER','时间区间无效')
    }
  }
  if(query.q){
    const fields=['name','title','slug','reason','message','source','strategy_id','status','problem','action','market_id','id'].filter(k=>columns.has(k))
    args.push(query.q.slice(0,200))
    where.push('('+ (fields.length?fields.map(k=>'strpos(lower('+k+'::text),lower($'+args.length+'::text))>0').join(' OR '):'length($'+args.length+'::text)=0')+')')
  }
  const date=['created_at','received_at','last_attempt_at','updated_at','first_seen_at','next_run_at'].find(k=>columns.has(k))
  const key=['seq','id','market_id','source','name','strategy_id','version','account_id'].find(k=>columns.has(k))
  const order=query.sort??(columns.has('seq')?'seq':date??key)
  if(!order||!columns.has(order)||!/^[a-z_]+$/.test(order))throw new ApiError(400,'SORT','排序字段无效')
  const scoped=where.length?' WHERE '+where.join(' AND '):''
  const sort=' ORDER BY '+order+' '+direction+(key&&key!==order?', '+key+' '+direction:'')
  const [data,count]=await Promise.all([
    db.query('SELECT * FROM '+table+scoped+sort+' LIMIT $'+(args.length+1)+' OFFSET $'+(args.length+2),[...args,limit,offset]),
    db.query('SELECT count(*) FROM '+table+scoped,args)
  ])
  return {data:data.rows,meta:{limit,offset,total:Number(count.rows[0].count)}}
}
export async function dashboard() {
  const [accounts, market, fills, curve, settlements, daily, byStrategy, byAsset, sources, events] = await Promise.all([
    db.query(`SELECT count(*)::int accounts,
      count(*) FILTER (WHERE status='RUNNING')::int running_accounts,
      (SELECT count(DISTINCT (s->>'strategy_id',s->>'strategy_version'))::int FROM paper_accounts p
        CROSS JOIN LATERAL jsonb_array_elements(jsonb_build_array(jsonb_build_object('strategy_id',p.strategy_id,'strategy_version',p.strategy_version)) || coalesce(p.config->'strategies','[]'::jsonb)) AS s
        WHERE p.mode='PAPER' AND p.status='RUNNING') AS running_strategies,
      sum(equity)::text total_equity, sum(equity-initial_balance)::text net_pnl,
      max(max_drawdown)::text max_drawdown FROM paper_accounts WHERE mode='PAPER'`),
    db.query("SELECT count(*)::int n FROM markets WHERE start_time<=now() AND end_time>now()"),
    db.query("SELECT count(*)::int n FROM paper_fills f JOIN paper_accounts a ON a.id=f.account_id WHERE a.mode='PAPER'"),
    db.query(`WITH dates AS (
      SELECT DISTINCT (e.created_at AT TIME ZONE 'UTC')::date AS day_utc FROM equity_snapshots e
      JOIN paper_accounts a ON a.id=e.account_id WHERE a.mode='PAPER' ORDER BY 1 DESC LIMIT 90
    ) SELECT day_utc::text AS time, sum(last.equity)::text equity FROM dates
      JOIN paper_accounts a ON a.mode='PAPER' AND a.created_at<((day_utc+1)::timestamp AT TIME ZONE 'UTC')
      CROSS JOIN LATERAL (SELECT equity FROM equity_snapshots e WHERE e.account_id=a.id
        AND e.created_at<((day_utc+1)::timestamp AT TIME ZONE 'UTC') ORDER BY e.created_at DESC,e.id DESC LIMIT 1) last
      GROUP BY day_utc ORDER BY day_utc`),
    db.query(`SELECT sum(s.realized_pnl) FILTER (WHERE (s.created_at AT TIME ZONE 'UTC')::date=(now() AT TIME ZONE 'UTC')::date)::text today_pnl,
      (count(*) FILTER (WHERE s.realized_pnl>0)::numeric/nullif(count(*),0))::text win_rate,
      count(*)::int sample_count FROM paper_settlements s JOIN paper_accounts a ON a.id=s.account_id WHERE a.mode='PAPER'`),
    db.query(`SELECT (s.created_at AT TIME ZONE 'UTC')::date::text time, sum(s.realized_pnl)::text net_pnl
      FROM paper_settlements s JOIN paper_accounts a ON a.id=s.account_id
      WHERE a.mode='PAPER' AND s.created_at>=now()-interval '90 days' GROUP BY 1 ORDER BY 1`),
    db.query(`SELECT composition.strategies, sum(s.realized_pnl)::text net_pnl,count(*)::int sample_count
      FROM paper_settlements s JOIN paper_accounts a ON a.id=s.account_id CROSS JOIN LATERAL (
        SELECT string_agg((member->>'strategy_id') || ' v' || (member->>'strategy_version'), ' + ' ORDER BY member->>'strategy_id') AS strategies
        FROM jsonb_array_elements(jsonb_build_array(jsonb_build_object('strategy_id',a.strategy_id,'strategy_version',a.strategy_version)) || coalesce(a.config->'strategies','[]'::jsonb)) AS member
      ) composition WHERE a.mode='PAPER' GROUP BY 1 ORDER BY 1`),
    db.query(`SELECT m.asset,sum(s.realized_pnl)::text net_pnl,count(*)::int sample_count
      FROM paper_settlements s JOIN paper_accounts a ON a.id=s.account_id JOIN markets m USING(market_id)
      WHERE a.mode='PAPER' GROUP BY 1 ORDER BY 1`),
    db.query('SELECT * FROM data_source_status ORDER BY source'),
    db.query("SELECT * FROM system_events WHERE upper(status) NOT IN ('INFO','OK','HEALTHY','READY','ONLINE') ORDER BY id DESC LIMIT 8"),
  ])
  return {...accounts.rows[0], ...settlements.rows[0], markets:market.rows[0].n, fills:fills.rows[0].n,
    series:curve.rows, daily_pnl:daily.rows, by_strategy:byStrategy.rows, by_asset:byAsset.rows,
    sources:sources.rows, events:events.rows}
}
