(() => {
  const state = {
    lotteries: [],
    currentCode: "ssq",
    analysisData: null,
    generatedTickets: [],
    nextIssue: "2026001",
  };

  const $ = (sel) => document.querySelector(sel);

  function showToast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(showToast._timer);
    showToast._timer = setTimeout(() => el.classList.remove("show"), 3200);
  }

  async function api(url, options = {}) {
    const resp = await fetch(url, options);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(data.detail || `请求失败 (${resp.status})`);
    }
    return data;
  }

  function currentRule() {
    return state.lotteries.find((l) => l.code === state.currentCode) || state.lotteries[0];
  }

  function formatBall(num, zone) {
    const label = zone.max_num >= 10 ? String(num).padStart(2, "0") : String(num);
    return `<span class="ball" style="background:${zone.color}">${label}</span>`;
  }

  function renderNumbersHtml(numbers, rule) {
    if (!rule || !numbers) return "-";
    return rule.zones
      .map((z) => (numbers[z.key] || []).map((n) => formatBall(n, z)).join(""))
      .join('<span style="margin:0 4px;color:#94a3b8;">|</span>');
  }

  function parseNums(raw) {
    return (raw.match(/\d+/g) || []).map((s) => parseInt(s, 10));
  }

  async function loadLotteries() {
    state.lotteries = await api("/api/lotteries");
    const container = $("#lotterySelector");
    container.innerHTML = "";
    state.lotteries.forEach((l) => {
      const btn = document.createElement("button");
      btn.className = `lottery-btn ${l.code === state.currentCode ? "active" : ""}`;
      btn.textContent = l.name;
      btn.onclick = () => selectLottery(l.code);
      container.appendChild(btn);
    });
    updateLotteryHeader();
    buildCustomEvalInputs();
  }

  function updateLotteryHeader() {
    const rule = currentRule();
    if (!rule) return;
    $("#drawCountBadge").textContent = `已存 ${rule.draw_count} 期`;
    state.nextIssue = rule.next_issue || "2026001";
    $("#purchaseIssueInput").value = state.nextIssue;
  }

  async function selectLottery(code) {
    state.currentCode = code;
    state.generatedTickets = [];
    $("#generatedTicketsList").innerHTML = "";
    $("#customEvalResult").innerHTML = "";
    await loadLotteries();
    await Promise.all([loadDraws(), loadAnalysis(), loadPurchases()]);
  }

  async function loadDraws() {
    const rule = currentRule();
    const data = await api(`/api/draws/${state.currentCode}?limit=100`);
    state.nextIssue = data.next_issue || state.nextIssue;
    $("#purchaseIssueInput").value = state.nextIssue;
    $("#drawCountBadge").textContent = `已存 ${data.total} 期`;
    const tbody = $("#drawsTableBody");
    if (!data.items.length) {
      tbody.innerHTML = `<tr><td colspan="3" style="color:#64748b;padding:24px;">暂无历史数据，请点击「在线同步最新开奖」或「生成离线模拟数据」</td></tr>`;
      return;
    }
    tbody.innerHTML = data.items
      .map(
        (d) => `
      <tr>
        <td><strong>${d.issue}</strong></td>
        <td>${d.draw_date || "-"}</td>
        <td>${renderNumbersHtml(d.numbers, rule)}</td>
      </tr>`
      )
      .join("");
  }

  async function loadAnalysis() {
    const rule = currentRule();
    const win = parseInt($("#analysisWindowSelect").value, 10);
    const data = await api(`/api/analysis/${state.currentCode}?window=${win}`);
    state.analysisData = data;

    const zoneSelect = $("#analysisZoneSelect");
    const prevZone = zoneSelect.value;
    zoneSelect.innerHTML = rule.zones
      .map((z) => `<option value="${z.key}">${z.label}</option>`)
      .join("");
    if (rule.zones.some((z) => z.key === prevZone)) {
      zoneSelect.value = prevZone;
    }

    const p = data.patterns;
    $("#patternCards").innerHTML = `
      <div class="metric-card"><div class="metric-label">分析样本期数</div><div class="metric-value">${p.total} 期</div></div>
      <div class="metric-card"><div class="metric-label">和值核心区间 (P25–P75)</div><div class="metric-value">${p.sum_p25} ~ ${p.sum_p75}</div></div>
      <div class="metric-card"><div class="metric-label">平均和值 / 平均跨度</div><div class="metric-value">${p.sum_avg} / ${p.span_avg}</div></div>
      <div class="metric-card"><div class="metric-label">最常见奇偶比</div><div class="metric-value">${p.odd_even_top[0] ? `${p.odd_even_top[0][0]} (${p.odd_even_top[0][2]}%)` : "-"}</div></div>
    `;

    renderZoneStats();
  }

  function renderZoneStats() {
    const rule = currentRule();
    if (!state.analysisData || !rule) return;
    const zKey = $("#analysisZoneSelect").value || rule.zones[0].key;
    const zone = rule.zones.find((z) => z.key === zKey) || rule.zones[0];
    const stats = state.analysisData.zones[zone.key] || [];

    $("#statsTableBody").innerHTML = stats
      .map(
        (s) => `
      <tr>
        <td>${formatBall(s.number, zone)}</td>
        <td>${s.frequency}</td>
        <td>${(s.empirical_prob * 100).toFixed(2)}%</td>
        <td>${(s.theory_prob * 100).toFixed(2)}%</td>
        <td>${s.current_omission}</td>
        <td>${s.avg_omission}</td>
        <td>${s.max_omission}</td>
        <td>${s.desire_index}</td>
        <td><strong>${s.score}</strong></td>
      </tr>`
      )
      .join("");

    drawFrequencyChart(stats, zone);
  }

  function drawFrequencyChart(stats, zone) {
    const canvas = $("#freqChartCanvas");
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.parentElement.clientWidth - 16;
    const height = 250;
    canvas.width = Math.max(width, 320) * dpr;
    canvas.height = height * dpr;
    canvas.style.height = `${height}px`;

    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    const w = Math.max(width, 320);
    const h = height;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, w, h);

    if (!stats.length || stats[0].total_draws === 0) {
      ctx.fillStyle = "#64748b";
      ctx.font = "14px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("暂无历史数据，请先在「数据中心」同步或导入数据", w / 2, h / 2);
      return;
    }

    const padL = 38, padR = 16, padT = 24, padB = 30;
    const plotW = w - padL - padR;
    const plotH = h - padT - padB;
    const maxFreq = Math.max(...stats.map((s) => s.frequency), 1);
    const expected = stats[0].total_draws * stats[0].theory_prob;
    const yMax = Math.max(maxFreq, expected) * 1.18;

    ctx.strokeStyle = "#cbd5e1";
    ctx.beginPath();
    ctx.moveTo(padL, padT);
    ctx.lineTo(padL, h - padB);
    ctx.lineTo(w - padR, h - padB);
    ctx.stroke();

    const step = plotW / stats.length;
    const barW = Math.max(step * 0.62, 6);
    stats.forEach((s, idx) => {
      const barH = (s.frequency / yMax) * plotH;
      const x = padL + idx * step + (step - barW) / 2;
      const y = h - padB - barH;
      ctx.fillStyle = zone.color;
      ctx.fillRect(x, y, barW, barH);

      ctx.fillStyle = "#475569";
      ctx.font = "10px sans-serif";
      ctx.textAlign = "center";
      const numText = zone.max_num >= 10 ? String(s.number).padStart(2, "0") : String(s.number);
      ctx.fillText(numText, x + barW / 2, h - padB + 14);
      if (s.frequency > 0) {
        ctx.fillText(String(s.frequency), x + barW / 2, y - 4);
      }
    });

    const expY = h - padB - (expected / yMax) * plotH;
    ctx.save();
    ctx.strokeStyle = "#0f172a";
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    ctx.moveTo(padL, expY);
    ctx.lineTo(w - padR, expY);
    ctx.stroke();
    ctx.restore();

    ctx.fillStyle = "#0f172a";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "right";
    ctx.fillText(`理论期望: ${expected.toFixed(1)} 次`, w - padR, Math.max(expY - 6, 14));
  }

  function buildCustomEvalInputs() {
    const rule = currentRule();
    if (!rule) return;
    const box = $("#customEvalInputs");
    box.innerHTML = rule.zones
      .map(
        (z) => `
      <label>
        ${z.label}（${z.min_num}-${z.max_num}，选 ${z.count} 个，空格或逗号分隔）
        <input type="text" data-zone-key="${z.key}" placeholder="例如: ${Array.from({ length: z.count }, (_, i) => z.min_num + i).join(" ")}" />
      </label>`
      )
      .join("");
  }

  function readCustomNumbers() {
    const rule = currentRule();
    const numbers = {};
    rule.zones.forEach((z) => {
      const inp = $(`#customEvalInputs input[data-zone-key="${z.key}"]`);
      const vals = parseNums(inp ? inp.value : "");
      numbers[z.key] = z.ordered ? vals : [...vals].sort((a, b) => a - b);
    });
    return numbers;
  }

  function renderGeneratedTickets() {
    const rule = currentRule();
    const list = $("#generatedTicketsList");
    if (!state.generatedTickets.length) {
      list.innerHTML = `<div class="ticket-meta">点击「生成推荐号码」查看结果</div>`;
      return;
    }
    list.innerHTML = state.generatedTickets
      .map(
        (tk, idx) => `
      <div class="ticket-item">
        <div>
          <div><strong>#${idx + 1}</strong> ${renderNumbersHtml(tk.numbers, rule)}</div>
          <div class="ticket-meta">评分 ${tk.score} · ${tk.pattern_summary}</div>
        </div>
        <button class="btn btn-sm btn-success" data-buy-idx="${idx}">记入模拟购彩</button>
      </div>`
      )
      .join("");

    list.querySelectorAll("button[data-buy-idx]").forEach((btn) => {
      btn.onclick = async () => {
        const idx = parseInt(btn.dataset.buyIdx, 10);
        const tk = state.generatedTickets[idx];
        await createPurchases([tk.numbers]);
      };
    });
  }

  async function createPurchases(numbersList) {
    const issue = ($("#purchaseIssueInput").value || state.nextIssue).trim();
    const betCount = parseInt($("#purchaseBetCountInput").value, 10) || 1;
    const res = await api(`/api/purchases/${state.currentCode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        items: numbersList.map((numbers) => ({
          target_issue: issue,
          numbers,
          bet_count: betCount,
        })),
      }),
    });
    showToast(`已记录 ${res.inserted} 注至第 ${issue} 期` + (res.settled_count ? `，自动兑奖 ${res.settled_count} 注` : ""));
    await loadPurchases();
  }

  async function loadPurchases() {
    const rule = currentRule();
    const data = await api(`/api/purchases/${state.currentCode}`);
    const s = data.summary;
    $("#purchaseMetrics").innerHTML = `
      <div class="metric-card"><div class="metric-label">模拟总注单</div><div class="metric-value">${s.total_records} 笔</div></div>
      <div class="metric-card"><div class="metric-label">待开奖</div><div class="metric-value">${s.pending_count} 笔</div></div>
      <div class="metric-card"><div class="metric-label">累计模拟投入</div><div class="metric-value">¥${s.total_cost}</div></div>
      <div class="metric-card"><div class="metric-label">累计中奖奖金</div><div class="metric-value" style="color:#16a34a;">¥${s.total_prize}</div></div>
    `;

    const tbody = $("#purchasesTableBody");
    if (!data.items.length) {
      tbody.innerHTML = `<tr><td colspan="7" style="color:#64748b;padding:24px;">暂无模拟购彩记录</td></tr>`;
      return;
    }
    tbody.innerHTML = data.items
      .map((p) => {
        const statusHtml =
          p.status === "settled"
            ? `<span class="status-pill status-ok">已开奖</span>`
            : `<span class="status-pill status-pending">待开奖</span>`;
        const prizeHtml =
          p.prize_amount > 0
            ? `<strong style="color:#dc2626;">${p.prize_tier} (¥${p.prize_amount})</strong>`
            : p.prize_tier || "-";
        return `
        <tr>
          <td><input type="checkbox" class="purchase-check" value="${p.id}" /></td>
          <td>${p.target_issue}</td>
          <td>${renderNumbersHtml(p.numbers, rule)}</td>
          <td>${p.bet_count} 倍 / ¥${p.cost}</td>
          <td>${statusHtml}</td>
          <td>${prizeHtml}</td>
          <td>${Object.keys(p.draw_numbers || {}).length ? renderNumbersHtml(p.draw_numbers, rule) : "-"}</td>
        </tr>`;
      })
      .join("");
  }

  async function loadScheduler() {
    const data = await api("/api/scheduler");
    const cfg = data.config;
    $("#schedEnabledInput").checked = !!cfg.enabled;
    $("#schedAutoSettleInput").checked = !!cfg.auto_settle;
    $("#schedIntervalSelect").value = String(cfg.interval_minutes);
    $("#schedLimitInput").value = String(cfg.sync_limit);

    const statusClass =
      data.last_status === "success"
        ? "status-ok"
        : data.last_status === "error"
        ? "status-error"
        : "status-pending";

    const rowsHtml = (data.last_results || [])
      .map(
        (r) => `
      <tr>
        <td>${r.lottery_name}</td>
        <td><span class="status-pill ${r.status === "ok" ? "status-ok" : "status-error"}">${r.status === "ok" ? "成功" : "失败"}</span></td>
        <td>${r.latest_issue || "-"}</td>
        <td>${r.message}</td>
      </tr>`
      )
      .join("");

    $("#schedulerStatusBox").innerHTML = `
      <div><strong>运行状态：</strong> <span class="status-pill ${statusClass}">${data.is_running ? "同步中..." : data.last_status}</span></div>
      <div class="mt-12"><strong>上次执行：</strong> ${data.last_run_at || "尚未执行"} &nbsp;|&nbsp; <strong>下次预计：</strong> ${data.next_run_at || "待定"}</div>
      <div class="mt-12"><strong>执行摘要：</strong> ${data.last_summary}</div>
      ${
        rowsHtml
          ? `<div class="table-wrap mt-12"><table><thead><tr><th>彩种</th><th>结果</th><th>最新期号</th><th>详情</th></tr></thead><tbody>${rowsHtml}</tbody></table></div>`
          : ""
      }
    `;
  }

  function exportTicketsAsImage() {
    const rule = currentRule();
    if (!state.generatedTickets.length) {
      showToast("请先生成推荐号码");
      return;
    }
    const rowH = 54;
    const width = 560;
    const height = 40 + state.generatedTickets.length * rowH;
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);

    state.generatedTickets.forEach((tk, rowIdx) => {
      let x = 28;
      const y = 38 + rowIdx * rowH;
      rule.zones.forEach((z, zIdx) => {
        if (zIdx > 0) x += 14;
        (tk.numbers[z.key] || []).forEach((n) => {
          ctx.beginPath();
          ctx.arc(x, y, 16, 0, Math.PI * 2);
          ctx.fillStyle = z.color;
          ctx.fill();
          ctx.fillStyle = "#ffffff";
          ctx.font = "bold 13px sans-serif";
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          const txt = z.max_num >= 10 ? String(n).padStart(2, "0") : String(n);
          ctx.fillText(txt, x, y + 1);
          x += 40;
        });
      });
    });

    const link = document.createElement("a");
    link.download = `${rule.code}_tickets.png`;
    link.href = canvas.toDataURL("image/png");
    link.click();
    showToast("已导出号码图片");
  }

  function bindEvents() {
    document.querySelectorAll(".nav-tab").forEach((tab) => {
      tab.onclick = () => {
        document.querySelectorAll(".nav-tab").forEach((t) => t.classList.remove("active"));
        document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
        tab.classList.add("active");
        $(`#panel-${tab.dataset.tab}`).classList.add("active");
        if (tab.dataset.tab === "analysis") renderZoneStats();
        if (tab.dataset.tab === "scheduler") loadScheduler();
      };
    });

    const doSync = async () => {
      const limit = parseInt($("#syncLimitInput").value, 10) || 100;
      try {
        showToast("正在同步最新开奖数据...");
        const res = await api(`/api/draws/${state.currentCode}/sync`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ limit }),
        });
        showToast(`同步成功：拉取 ${res.fetched} 期` + (res.settled_count ? `，自动兑奖 ${res.settled_count} 注` : ""));
        await Promise.all([loadLotteries(), loadDraws(), loadAnalysis(), loadPurchases()]);
      } catch (err) {
        showToast(err.message);
      }
    };

    $("#quickSyncBtn").onclick = doSync;
    $("#btnSyncOnline").onclick = doSync;

    $("#btnGenerateMock").onclick = async () => {
      const limit = parseInt($("#syncLimitInput").value, 10) || 120;
      try {
        const res = await api(`/api/draws/${state.currentCode}/mock`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ limit }),
        });
        showToast(`已生成 ${res.generated} 期模拟数据`);
        await Promise.all([loadLotteries(), loadDraws(), loadAnalysis(), loadPurchases()]);
      } catch (err) {
        showToast(err.message);
      }
    };

    $("#importFileInput").onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const fd = new FormData();
      fd.append("file", file);
      try {
        const res = await api(`/api/draws/${state.currentCode}/import`, {
          method: "POST",
          body: fd,
        });
        showToast(`成功导入 ${res.imported} 期数据`);
        await Promise.all([loadLotteries(), loadDraws(), loadAnalysis(), loadPurchases()]);
      } catch (err) {
        showToast(err.message);
      } finally {
        e.target.value = "";
      }
    };

    $("#btnClearDraws").onclick = async () => {
      if (!confirm("确认清空当前彩种的全部历史开奖记录吗？")) return;
      await api(`/api/draws/${state.currentCode}`, { method: "DELETE" });
      showToast("已清空历史开奖记录");
      await Promise.all([loadLotteries(), loadDraws(), loadAnalysis()]);
    };

    $("#analysisWindowSelect").onchange = loadAnalysis;
    $("#analysisZoneSelect").onchange = renderZoneStats;

    $("#btnGenerateTickets").onclick = async () => {
      const sumMinRaw = $("#genSumMinInput").value.trim();
      const sumMaxRaw = $("#genSumMaxInput").value.trim();
      try {
        const res = await api(`/api/generate/${state.currentCode}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            strategy: $("#genStrategySelect").value,
            count: parseInt($("#genCountInput").value, 10) || 5,
            window: parseInt($("#genWindowInput").value, 10) || 0,
            deterministic: $("#genModeSelect").value === "deterministic",
            sum_min: sumMinRaw ? parseInt(sumMinRaw, 10) : null,
            sum_max: sumMaxRaw ? parseInt(sumMaxRaw, 10) : null,
          }),
        });
        state.generatedTickets = res.tickets;
        renderGeneratedTickets();
      } catch (err) {
        showToast(err.message);
      }
    };

    $("#btnCopyText").onclick = async () => {
      const rule = currentRule();
      if (!state.generatedTickets.length) return showToast("请先生成推荐号码");
      const text = state.generatedTickets
        .map((tk) =>
          rule.zones
            .map((z) => `${z.label}: ${(tk.numbers[z.key] || []).map((n) => (z.max_num >= 10 ? String(n).padStart(2, "0") : n)).join(" ")}`)
            .join(" | ")
        )
        .join("\n");
      await navigator.clipboard.writeText(text);
      showToast("已复制推荐号码到剪贴板");
    };

    $("#btnDownloadImg").onclick = exportTicketsAsImage;

    $("#btnBuyAllGenerated").onclick = async () => {
      if (!state.generatedTickets.length) return showToast("请先生成推荐号码");
      try {
        await createPurchases(state.generatedTickets.map((t) => t.numbers));
      } catch (err) {
        showToast(err.message);
      }
    };

    $("#btnEvaluateCustom").onclick = async () => {
      try {
        const numbers = readCustomNumbers();
        const res = await api(`/api/evaluate/${state.currentCode}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ numbers, window: 100 }),
        });
        $("#customEvalResult").innerHTML = `
          <div><strong>综合契合度：${res.overall_fit} 分</strong>（单号均分 ${res.avg_num_score}）</div>
          <div class="mt-12">和值：${res.features.sum} · 奇偶比：${res.features.odd_even} · 大小比：${res.features.size_ratio} · 跨度：${res.features.span}</div>
          <ul style="margin:8px 0 0;padding-left:18px;">
            ${res.diagnostics.map((d) => `<li>${d}</li>`).join("")}
          </ul>
        `;
      } catch (err) {
        showToast(err.message);
      }
    };

    $("#btnBuyCustom").onclick = async () => {
      try {
        await createPurchases([readCustomNumbers()]);
      } catch (err) {
        showToast(err.message);
      }
    };

    $("#btnSettleNow").onclick = async () => {
      const res = await api(`/api/purchases/${state.currentCode}/settle`, { method: "POST" });
      showToast(`核对完成：结算 ${res.settled_count} 注，中奖 ¥${res.settled_prize}`);
      await loadPurchases();
    };

    $("#selectAllPurchases").onchange = (e) => {
      document.querySelectorAll(".purchase-check").forEach((c) => (c.checked = e.target.checked));
    };

    $("#btnDeleteSelectedPurchases").onclick = async () => {
      const ids = Array.from(document.querySelectorAll(".purchase-check:checked")).map((c) => parseInt(c.value, 10));
      if (!ids.length) return showToast("请先勾选要删除的记录");
      await api("/api/purchases/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      });
      showToast(`已删除 ${ids.length} 条记录`);
      await loadPurchases();
    };

    $("#btnRunBacktest").onclick = async () => {
      try {
        showToast("正在运行滚动回测...");
        const res = await api(`/api/backtest/${state.currentCode}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            strategy: $("#btStrategySelect").value,
            train_window: parseInt($("#btTrainInput").value, 10) || 50,
            test_periods: parseInt($("#btTestInput").value, 10) || 20,
            tickets_per_draw: parseInt($("#btTicketsInput").value, 10) || 5,
          }),
        });
        const distRows = Object.entries(res.hit_distribution)
          .map(([k, v]) => `<tr><td>${k}</td><td>${v} 注</td></tr>`)
          .join("");
        $("#backtestResult").innerHTML = `
          <div class="metrics-grid">
            <div class="metric-card"><div class="metric-label">回测期数</div><div class="metric-value">${res.test_periods} 期</div></div>
            <div class="metric-card"><div class="metric-label">总模拟注数</div><div class="metric-value">${res.total_tickets} 注</div></div>
            <div class="metric-card"><div class="metric-label">主区平均命中</div><div class="metric-value">${res.avg_primary_hits} 个</div></div>
          </div>
          <div class="table-wrap"><table><thead><tr><th>命中分布</th><th>注数</th></tr></thead><tbody>${distRows}</tbody></table></div>
          ${
            res.highlights.length
              ? `<div class="eval-result mt-12"><strong>高命中记录：</strong><ul style="margin:6px 0 0;padding-left:18px;">${res.highlights
                  .map((h) => `<li>${h}</li>`)
                  .join("")}</ul></div>`
              : ""
          }
        `;
      } catch (err) {
        showToast(err.message);
      }
    };

    $("#btnSaveScheduler").onclick = async () => {
      await api("/api/scheduler", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: $("#schedEnabledInput").checked,
          auto_settle: $("#schedAutoSettleInput").checked,
          interval_minutes: parseInt($("#schedIntervalSelect").value, 10),
          sync_limit: parseInt($("#schedLimitInput").value, 10),
        }),
      });
      showToast("定时同步配置已保存");
      await loadScheduler();
    };

    $("#btnRunSchedulerNow").onclick = async () => {
      showToast("正在同步全部彩种并自动兑奖...");
      await api("/api/scheduler/run", { method: "POST" });
      showToast("全彩种同步与兑奖已完成");
      await Promise.all([loadLotteries(), loadDraws(), loadPurchases(), loadScheduler()]);
    };

    window.addEventListener("resize", () => {
      if ($("#panel-analysis").classList.contains("active")) renderZoneStats();
    });
  }

  async function init() {
    bindEvents();
    await loadLotteries();
    await Promise.all([loadDraws(), loadAnalysis(), loadPurchases(), loadScheduler()]);
  }

  init().catch((err) => showToast(err.message));
})();
