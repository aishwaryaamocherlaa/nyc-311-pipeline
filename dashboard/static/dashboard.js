// ----- State -----
const state = { districts: [], complaint_types: [], date_from: null, date_to: null };

// ----- Helpers -----
function buildQuery() {
  const p = new URLSearchParams();
  state.districts.forEach(d => p.append("district", d));
  state.complaint_types.forEach(c => p.append("complaint_type", c));
  if (state.date_from) p.append("date_from", state.date_from);
  if (state.date_to) p.append("date_to", state.date_to);
  return p.toString();
}
async function api(path) {
  const r = await fetch(path + "?" + buildQuery());
  if (!r.ok) throw new Error("API " + path + " failed");
  return r.json();
}
const layoutBase = { margin: { l: 50, r: 20, t: 20, b: 50 }, font: { size: 11 } };

// ----- Init: filters -----
async function initFilters() {
  const opts = await fetch("/api/options").then(r => r.json());
  const dSel = document.getElementById("filter-district");
  opts.districts.forEach(d => dSel.add(new Option("District " + d, d)));
  const cSel = document.getElementById("filter-complaint");
  opts.complaint_types.forEach(c => cSel.add(new Option(c, c)));
  document.getElementById("filter-date-from").min = opts.date_min;
  document.getElementById("filter-date-from").max = opts.date_max;
  document.getElementById("filter-date-to").min = opts.date_min;
  document.getElementById("filter-date-to").max = opts.date_max;
  document.getElementById("filter-date-from").value = opts.date_min;
  document.getElementById("filter-date-to").value = opts.date_max;
  state.date_from = opts.date_min; state.date_to = opts.date_max;
}

function readFilters() {
  state.districts = Array.from(document.getElementById("filter-district").selectedOptions).map(o => o.value);
  state.complaint_types = Array.from(document.getElementById("filter-complaint").selectedOptions).map(o => o.value);
  state.date_from = document.getElementById("filter-date-from").value || null;
  state.date_to = document.getElementById("filter-date-to").value || null;
}
async function clearFilters() {
  document.getElementById("filter-district").selectedIndex = -1;
  document.getElementById("filter-complaint").selectedIndex = -1;
  const opts = await fetch("/api/options").then(r => r.json());
  document.getElementById("filter-date-from").value = opts.date_min;
  document.getElementById("filter-date-to").value = opts.date_max;
  state.districts = [];
  state.complaint_types = [];
  state.date_from = opts.date_min;
  state.date_to = opts.date_max;
  renderAll();
}

// ----- KPI -----
async function renderKPI() {
  const k = await api("/api/kpi");
  document.getElementById("kpi-volume").textContent = (k.total_volume).toLocaleString();
  document.getElementById("kpi-closure").textContent = k.closure_rate + "%";
  document.getElementById("kpi-resolution").textContent = k.avg_resolution_hours;
  const aging = (k.overdue_open || []).reduce((s, r) => s + (r.volume || 0), 0);
  document.getElementById("kpi-overdue").textContent = aging.toLocaleString();
}

// ----- Map (G11) -----
async function renderMap() {
  const d = await api("/api/g11_map");
  const trace = {
    type: "scattermapbox",
    mode: "markers",
    lat: d.map(r => r.avg_latitude),
    lon: d.map(r => r.avg_longitude),
    text: d.map(r =>
      `<b>District ${r.council_district}</b><br>` +
      `Area: ${r.primary_community_board}<br>` +
      `Volume: ${r.total_volume.toLocaleString()}<br>` +
      `Avg resolution: ${r.avg_resolution_hours} hrs<br>` +
      `Closure rate: ${(r.closure_rate * 100).toFixed(1)}%<br>` +
      `Top complaint: ${r.top_complaint_type}`
    ),
    hoverinfo: "text",
    marker: {
      size: d.map(r => Math.sqrt(r.total_volume) / 2),
      color: d.map(r => r.avg_resolution_hours),
      colorscale: "Viridis", showscale: true, sizemode: "diameter",
      colorbar: { title: "Avg hrs", thickness: 10 },
    },
  };
  Plotly.newPlot("chart-map", [trace], {
    ...layoutBase,
    mapbox: { style: "open-street-map", center: { lat: 40.73, lon: -73.95 }, zoom: 9.2 },
    margin: { l: 0, r: 0, t: 0, b: 0 },
  }, { responsive: true });
}

// ----- G4 Temporal -----
async function renderG4() {
  const d = await api("/api/g4_temporal");
  // Format created_date_partition as 'Apr 01' style — no time, no day name
  const formatDate = (val) => {
    const dt = new Date(val);
    const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const day = String(dt.getUTCDate()).padStart(2, "0");
    return `${months[dt.getUTCMonth()]} ${day}`;
  };
  const xLabels = d.map(r => formatDate(r.created_date_partition));

  Plotly.newPlot("chart-g4", [{
    x: xLabels,
    y: d.map(r => r.total_volume),
    type: "scatter",
    mode: "lines+markers",
    name: "Volume",
    line: { color: "#2563eb", width: 2 },
    hovertemplate: "%{x}<br>Volume: %{y:,}<extra></extra>",
  }], {
    margin: { l: 60, r: 20, t: 20, b: 80 },
    font: { size: 11 },
    xaxis: {
      title: { text: "Date", standoff: 40 },
      tickangle: -45,
      automargin: true,
      type: "category",  // forces Plotly to treat labels as strings, not parse as dates
    },
    yaxis: { title: "Daily Volume", automargin: true },
  }, { responsive: true });
}

// ----- G7 Heatmap -----
async function renderG7() {
  const d = await api("/api/g7_heatmap");
  const days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const hours = [...Array(24).keys()];
  const formatHour = (h) => {
    if (h === 0) return "12 am";
    if (h === 12) return "12 pm";
    if (h < 12) return `${h} am`;
    return `${h - 12} pm`;
  };
  const hourLabels = hours.map(formatHour);
  const z = days.map(day =>
    hours.map(h => {
      const row = d.find(r => r.created_day_of_week === day && r.created_hour === h);
      return row ? row.volume : 0;
    })
  );
  Plotly.newPlot("chart-g7", [{
    z,
    x: hourLabels,
    y: days,
    type: "heatmap",
    colorscale: "YlOrRd",
    hovertemplate: "%{y} · %{x}<br>Volume: %{z:,}<extra></extra>",
  }], {
    margin: { l: 95, r: 30, t: 10, b: 100 },
    font: { size: 11 },
    xaxis: {
      title: { text: "Hour of Day", standoff: 50 },
      tickangle: -45,
      automargin: true,
    },
    yaxis: { title: "", automargin: true },
  }, { responsive: true });
}

// ----- G1 Districts -----
async function renderG1() {
  const d = await api("/api/g1_districts");
  Plotly.newPlot("chart-g1", [{
    x: d.map(r => "D" + r.council_district),
    y: d.map(r => r.total_volume),
    type: "bar",
    marker: { color: "#0a2540" },
    customdata: d.map(r => [r.council_district, r.primary_community_board, r.avg_resolution_hours, r.median_resolution_hours]),
    hovertemplate:
      "<b>Council District %{customdata[0]}</b><br>" +
      "Area: %{customdata[1]}<br>" +
      "Volume: %{y:,}<br>" +
      "Avg resolution: %{customdata[2]} hrs<br>" +
      "Median resolution: %{customdata[3]} hrs" +
      "<extra></extra>",
  }], {
    ...layoutBase,
    xaxis: { title: "Council District", tickangle: 0 },
    yaxis: { title: "Volume" },
  }, { responsive: true });
}

// ----- G5 Bottleneck -----
async function renderG5() {
  const d = await api("/api/g5_bottleneck");
  Plotly.newPlot("chart-g5", [{
    x: d.map(r => "D" + r.council_district),
    y: d.map(r => r.open_to_closed_ratio),
    type: "bar",
    marker: { color: d.map(r => r.exceeds_citywide ? "#dc2626" : "#9ca3af") },
    customdata: d.map(r => [r.council_district, r.primary_community_board, r.open_volume, r.closed_volume, r.citywide_ratio]),
    hovertemplate:
      "<b>Council District %{customdata[0]}</b><br>" +
      "Area: %{customdata[1]}<br>" +
      "Open: %{customdata[2]:,}<br>" +
      "Closed: %{customdata[3]:,}<br>" +
      "Ratio: %{y}<br>" +
      "Citywide avg: %{customdata[4]}" +
      "<extra></extra>",
  }], {
    ...layoutBase,
    xaxis: { title: "Council District", tickangle: 0 },
    yaxis: { title: "Open / Closed Ratio" },
    shapes: d.length ? [{
      type: "line", xref: "paper", x0: 0, x1: 1,
      y0: d[0].citywide_ratio, y1: d[0].citywide_ratio,
      line: { color: "#2563eb", dash: "dash", width: 2 },
    }] : [],
  }, { responsive: true });
}

// ----- G2 Complaints -----
async function renderG2() {
  const d = await api("/api/g2_complaints");
  Plotly.newPlot("chart-g2", [{
    y: d.map(r => r.complaint_type), x: d.map(r => r.citywide_volume),
    type: "bar", orientation: "h", marker: { color: "#0a2540" },
  }], { ...layoutBase, margin: { l: 180, r: 20, t: 20, b: 50 }, xaxis: { title: "Volume" } }, { responsive: true });
}

// ----- G9 Hotspots -----
async function renderG9() {
  const d = await api("/api/g9_hotspots");
  Plotly.newPlot("chart-g9", [{
    y: d.slice(0, 25).map(r => r.complaint_type + " · " + r.incident_zip),
    x: d.slice(0, 25).map(r => r.volume),
    type: "bar", orientation: "h", marker: { color: "#4f46e5" },
  }], { ...layoutBase, margin: { l: 220, r: 20, t: 20, b: 50 }, xaxis: { title: "Volume" } }, { responsive: true });
}

// ----- G3 Agencies -----
async function renderG3() {
  const d = await api("/api/g3_agencies");
  Plotly.newPlot("chart-g3", [
    {
      x: d.map(r => r.agency),
      y: d.map(r => r.total_volume),
      type: "bar",
      name: "Volume",
      marker: { color: "#0a2540" },
      yaxis: "y",
      customdata: d.map(r => [r.agency_name, r.avg_resolution_hours]),
      hovertemplate:
        "<b>%{x}</b><br>" +
        "%{customdata[0]}<br>" +
        "Volume: %{y:,}<br>" +
        "Avg resolution: %{customdata[1]} hrs" +
        "<extra></extra>",
    },
    {
      x: d.map(r => r.agency),
      y: d.map(r => r.closure_rate * 100),
      type: "scatter",
      mode: "markers",
      name: "Closure %",
      marker: { color: "#10b981", size: 10 },
      yaxis: "y2",
      customdata: d.map(r => [r.agency_name]),
      hovertemplate:
        "<b>%{x}</b><br>" +
        "%{customdata[0]}<br>" +
        "Closure rate: %{y:.1f}%" +
        "<extra></extra>",
    },
  ], {
    margin: { l: 60, r: 60, t: 30, b: 50 },
    font: { size: 11 },
    yaxis: { title: "Volume", automargin: true },
    yaxis2: { title: "Closure %", overlaying: "y", side: "right", range: [0, 100], automargin: true },
    legend: { x: 0, y: 1.15, orientation: "h" },
    xaxis: { title: "Agency", tickangle: 0 },
  }, { responsive: true });
}

// ----- G6 SLA -----
async function renderG6() {
  const d = await api("/api/g6_sla");
  Plotly.newPlot("chart-g6", [{
    x: d.map(r => r.agency),
    y: d.map(r => r.sla_compliance_pct),
    type: "bar",
    marker: { color: "#10b981" },
    customdata: d.map(r => [r.agency_name, r.closed_with_sla, r.within_sla_count]),
    hovertemplate:
      "<b>%{x}</b><br>" +
      "%{customdata[0]}<br>" +
      "Within SLA: %{customdata[2]:,} of %{customdata[1]:,}<br>" +
      "Compliance: %{y:.2f}%" +
      "<extra></extra>",
  }], {
    ...layoutBase,
    yaxis: { title: "SLA compliance %", range: [0, 100] },
    xaxis: { title: "Agency" },
  }, { responsive: true });
}

// ----- G10 Aging -----
async function renderG10() {
  const d = await api("/api/g10_aging");
  Plotly.newPlot("chart-g10", [{
    x: d.map(r => r.age_bucket), y: d.map(r => r.volume),
    type: "bar", marker: { color: ["#9ca3af","#fbbf24","#f97316","#dc2626","#7f1d1d"] },
  }], { ...layoutBase, xaxis: { title: "Time open" }, yaxis: { title: "Tickets" } }, { responsive: true });
}

// ----- G8 Channels -----
async function renderG8() {
  const d = await api("/api/g8_channels");
  const districts = [...new Set(d.map(r => r.council_district))].sort((a, b) => a - b);
  const districtAreaLookup = {};
  d.forEach(r => { districtAreaLookup[r.council_district] = r.primary_community_board; });
  const channels = [...new Set(d.map(r => r.open_data_channel_type))];
  const traces = channels.map(ch => ({
    x: districts.map(dist => "D" + dist),
    y: districts.map(dist => {
      const row = d.find(r => r.council_district === dist && r.open_data_channel_type === ch);
      return row ? row.volume : 0;
    }),
    name: ch,
    type: "bar",
    customdata: districts.map(dist => [dist, districtAreaLookup[dist] || ""]),
    hovertemplate:
      "<b>Council District %{customdata[0]}</b><br>" +
      "Area: %{customdata[1]}<br>" +
      ch + ": %{y:,}" +
      "<extra></extra>",
  }));
  Plotly.newPlot("chart-g8", traces, {
    margin: { l: 60, r: 20, t: 50, b: 90 },
    font: { size: 11 },
    barmode: "stack",
    xaxis: {
      title: { text: "Council District", standoff: 50 },
      tickangle: -45,
      automargin: true,
    },
    yaxis: { title: "Volume", automargin: true },
    legend: { orientation: "h", y: 1.15 },
  }, { responsive: true });
}

async function renderAll() {
  await Promise.all([
    renderKPI(), renderMap(), renderG4(), renderG7(),
    renderG1(), renderG5(), renderG2(), renderG9(),
    renderG3(), renderG6(), renderG10(), renderG8(),
  ]);
}

document.getElementById("btn-apply").addEventListener("click", () => { readFilters(); renderAll(); });
document.getElementById("btn-clear").addEventListener("click", clearFilters);
document.getElementById("filter-date-from").addEventListener("change", () => { readFilters(); renderAll(); });
document.getElementById("filter-date-to").addEventListener("change", () => { readFilters(); renderAll(); });

(async () => {
  await initFilters();
  await renderAll();
})();