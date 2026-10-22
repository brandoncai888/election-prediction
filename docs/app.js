"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const state = { summary: null, races: [], groups: {}, detail: null, selectedId: null, office: "", state: "", search: "", request: 0, expanded: false, baseline: null, scenario: null, scenarioRequest: 0, scenarioTimer: null, mapOffice:"house", predictions:{}, mapRequest:0, geography:null, cohortExpanded:false, table:{office:"senate",sort:"closeness",dir:1,search:"",request:0} };
  const marginColors = {safe_d:"#1c408c",likely_d:"#577ccc",lean_d:"#8aafff",tilt_d:"#949bb3",tossup:"#9e8767",tilt_r:"#cf8980",lean_r:"#ff8b98",likely_r:"#ff5865",safe_r:"#bf1d29",unavailable:"#d9d9d9",holdover_d:"#0f2a63",holdover_r:"#8a1420"};
  const ratingOrder = ["safe_d","likely_d","lean_d","tilt_d","tossup","tilt_r","lean_r","likely_r","safe_r"];
  const ratingLabel = rating => ({safe_d:"Safe D",likely_d:"Likely D",lean_d:"Lean D",tilt_d:"Tilt D",tossup:"Toss-up",tilt_r:"Tilt R",lean_r:"Lean R",likely_r:"Likely R",safe_r:"Safe R"}[rating] || "No estimate");
  const winPct = value => finite(value) ? `${value >= .995 ? ">99" : value <= .005 ? "<1" : (value*100).toFixed(0)}%` : "—";
  const palette = ["#246457", "#8ca27b", "#b6bd96", "#d2bd8b", "#9caa9e", "#b99b82", "#5d8382", "#c8c7ac"];
  const numberFormat = new Intl.NumberFormat("en-US");
  const stateNames = {AL:"Alabama",AK:"Alaska",AZ:"Arizona",AR:"Arkansas",CA:"California",CO:"Colorado",CT:"Connecticut",DE:"Delaware",DC:"District of Columbia",FL:"Florida",GA:"Georgia",HI:"Hawaii",ID:"Idaho",IL:"Illinois",IN:"Indiana",IA:"Iowa",KS:"Kansas",KY:"Kentucky",LA:"Louisiana",ME:"Maine",MD:"Maryland",MA:"Massachusetts",MI:"Michigan",MN:"Minnesota",MS:"Mississippi",MO:"Missouri",MT:"Montana",NE:"Nebraska",NV:"Nevada",NH:"New Hampshire",NJ:"New Jersey",NM:"New Mexico",NY:"New York",NC:"North Carolina",ND:"North Dakota",OH:"Ohio",OK:"Oklahoma",OR:"Oregon",PA:"Pennsylvania",RI:"Rhode Island",SC:"South Carolina",SD:"South Dakota",TN:"Tennessee",TX:"Texas",UT:"Utah",VT:"Vermont",VA:"Virginia",WA:"Washington",WV:"West Virginia",WI:"Wisconsin",WY:"Wyoming",AS:"American Samoa",GU:"Guam",MP:"Northern Mariana Islands",PR:"Puerto Rico",VI:"US Virgin Islands"};
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const fmt = value => finite(value) ? numberFormat.format(Math.round(value)) : "—";
  const pct = value => finite(value) ? `${(value * 100).toFixed(1)}%` : "—";
  const pp = value => finite(value) ? `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(1)} pp` : "—";
  const titleCase = value => String(value ?? "").replace(/[_-]/g, " ").replace(/\b\w/g, char => char.toUpperCase());
  const officeLabel = value => ({house:"US House",senate:"US Senate",governor:"Governor",president:"President"}[String(value).toLowerCase()] || titleCase(value));
  const raceState = race => String(race.state || "").toUpperCase();
  const raceOffice = race => String(race.office || "").toLowerCase();
  const rows = key => Array.isArray(state.detail?.[key]) ? state.detail[key] : [];
  const safeUrl = value => { try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) ? url.href : null; } catch { return null; } };
  const date = value => { if (!value) return "Date not recorded"; const parsed = new Date(String(value).length === 10 ? `${value}T12:00:00` : value); return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleDateString("en-US", {year:"numeric",month:"short",day:"numeric"}); };
  const show = (id, visible) => { $(id).hidden = !visible; };
  const announce = message => { $("live-status").textContent = message; };

  // A static export (python main.py export-site) replaces /api/ with JSON files.
  const STATIC = Boolean(document.querySelector('meta[name="election-static"]'));
  let sharedNews = null;

  function dataPath(path) {
    if (!STATIC) return path;
    const url = new URL(path, "http://local");
    const query = name => url.searchParams.get(name) || "";
    if (url.pathname === "/api/race") return `data/race/${encodeURIComponent(query("id"))}.json`;
    if (url.pathname === "/api/predictions") return `data/predictions-${encodeURIComponent(query("office"))}.json`;
    if (url.pathname.startsWith("/api/")) return `data/${url.pathname.slice(5)}.json`;
    return path.replace(/^\//, "");
  }

  // Undo the compaction in electiondb/site.py.
  async function expandDetail(detail) {
    if (!STATIC) return detail;
    const groups = detail.projection?.groups || [];
    if (!detail.forecasts) detail.forecasts = groups;
    if (detail.demographics_from_groups) detail.demographics = groups.map(group => ({...group, ...detail.demographics_from_groups}));
    if (detail.news_shared) detail.news = (sharedNews ||= (await api("/api/news")).news);
    return detail;
  }

  async function api(path) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch(dataPath(path), {signal:controller.signal, headers:{Accept:"application/json"}});
      if (!response.ok) throw new Error(`The data service returned ${response.status}.`);
      return await response.json();
    } catch (error) {
      if (error.name === "AbortError") throw new Error("The data request timed out. Please try again.");
      throw error;
    } finally { window.clearTimeout(timeout); }
  }

  function notice(message) {
    $("app-notice").replaceChildren();
    $("app-notice").append(document.createTextNode(message));
    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = "Retry";
    retry.addEventListener("click", initialize, {once:true});
    $("app-notice").append(retry);
    show("app-notice", true);
  }

  function renderSummary() {
    const summary = state.summary || {};
    const counts = summary.counts || {};
    $("metric-races").textContent = fmt(counts.races ?? state.races.length);
    $("metric-demographics").textContent = fmt(counts.demographic_cells);
    $("metric-history").textContent = fmt(counts.published_historical_cells ?? counts.historical_cells);
    if (finite(counts.historical_cells)) $("history-coverage").textContent = `Of ${fmt(counts.historical_cells)} stored cells; low samples suppressed`;
    $("metric-news").textContent = fmt(counts.news_items);
    $("as-of").textContent = summary.as_of ? `Database as of ${date(summary.as_of)}` : "2026 election cycle";
    const sources = Array.isArray(summary.sources) ? summary.sources : [];
    $("sources-list").innerHTML = sources.length ? sources.map(source => {
      const url = safeUrl(source.url);
      return `<div class="source-item">${url ? `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(source.name)} ↗</a>` : `<span>${escape(source.name)}</span>`}${source.notes ? `<p>${escape(source.notes)}</p>` : ""}</div>`;
    }).join("") : "<p>Source information is not available from the database yet.</p>";
    const limits = Array.isArray(summary.limitations) ? summary.limitations : [];
    if (limits.length) $("limitations-list").innerHTML = limits.map(limit => `<li>${escape(limit)}</li>`).join("");
  }

  function populateStates() {
    const states = [...new Set(state.races.map(raceState).filter(Boolean))].sort((a,b) => (stateNames[a] || a).localeCompare(stateNames[b] || b));
    $("state-filter").innerHTML = '<option value="">All states &amp; territories</option>' + states.map(code => `<option value="${escape(code)}">${escape(stateNames[code] || code)}</option>`).join("");
    $("state-filter").value = state.state;
  }

  function renderRaceList() {
    const query = state.search.toLowerCase().trim();
    const visible = state.races.filter(race => (!state.office || raceOffice(race) === state.office) && (!state.state || raceState(race) === state.state) && (!query || `${race.label} ${race.state} ${stateNames[raceState(race)] || ""} ${race.district ?? ""} ${race.office}`.toLowerCase().includes(query)));
    $("race-count").textContent = `${fmt(visible.length)} ${visible.length === 1 ? "race" : "races"} available`;
    $("race-list").setAttribute("aria-busy", "false");
    $("race-list").replaceChildren();
    if (!visible.length) {
      const empty = document.createElement("div"); empty.className = "list-loading";
      empty.textContent = "No races match these filters."; $("race-list").append(empty); return;
    }
    const fragment = document.createDocumentFragment();
    visible.forEach(race => {
      const button = document.createElement("button"); button.type = "button"; button.className = "race-option";
      button.setAttribute("aria-current", String(String(race.race_id) === String(state.selectedId)));
      const sublabel = raceOffice(race) === "house" ? `${officeLabel(race.office)} · ${Number(race.district) === 0 ? "At-large" : `District ${race.district ?? "—"}`}` : `${officeLabel(race.office)} · ${titleCase(race.election_type || "General")}`;
      button.innerHTML = `<span><span class="race-option-name">${escape(race.label || `${stateNames[raceState(race)] || race.state} ${officeLabel(race.office)}`)}</span><span class="race-option-meta">${escape(sublabel)}</span></span><span class="race-option-arrow" aria-hidden="true">↗</span>`;
      button.addEventListener("click", () => selectRace(race)); fragment.append(button);
    });
    $("race-list").append(fragment);
  }

  async function selectRace(race) {
    const id = race.race_id;
    state.selectedId = id; state.expanded = false; state.scenario = null; state.scenarioRequest++;
    window.clearTimeout(state.scenarioTimer);
    const request = ++state.request;
    renderRaceList();
    updateMapSelection();
    $("race-detail").setAttribute("aria-busy", "true");
    $("detail-loading").textContent = `Loading ${race.label || "race"}…`;
    show("detail-loading", true); show("detail-content", false);
    try {
      const detail = await expandDetail(await api(`/api/race?id=${encodeURIComponent(id)}`));
      if (request !== state.request) return;
      state.detail = detail;
      renderDetail();
      show("detail-content", true); show("detail-loading", false);
      announce(`${detail.race?.label || race.label} loaded.`);
    } catch (error) {
      if (request !== state.request) return;
      $("detail-loading").textContent = `Could not load this race. ${error.message}`;
      notice("The race detail could not be loaded. Your data has not been changed.");
    } finally {
      if (request === state.request) $("race-detail").setAttribute("aria-busy", "false");
    }
  }

  function renderDetail() {
    const race = state.detail.race || {};
    $("race-kicker").textContent = `2026 ${String(race.election_type || "general").replace(/_/g," ").toUpperCase()} ELECTION`;
    $("race-title").textContent = race.label || "Selected race";
    $("race-subtitle").textContent = `${stateNames[raceState(race)] || race.state || "Constituency"} · ${race.election_date ? date(race.election_date) : "Election date not recorded"}`;
    $("race-office").textContent = officeLabel(race.office);
    $("boundary-note").textContent = race.boundary_note || "";
    show("boundary-note", Boolean(race.boundary_note));
    renderPopulation(); populateGroups(); renderHistory(); renderProjection(); renderNews();
  }

  function groupOptions(field) {
    const key = {race_group:"race_groups",sex:"sexes",education:"education"}[field];
    const source = Array.isArray(state.groups[key]) ? state.groups[key] : [];
    const options = new Map(source.map(item => typeof item === "string" ? [item, titleCase(item)] : [String(item.value), item.label || titleCase(item.value)]));
    [...rows("demographics"), ...rows("history"), ...rows("forecasts")].forEach(row => { if (row[field] != null && !options.has(String(row[field]))) options.set(String(row[field]), titleCase(row[field])); });
    return [...options].map(([value,label]) => ({value,label}));
  }

  function groupLabel(field, value) {
    return groupOptions(field).find(item => item.value === String(value))?.label || titleCase(value);
  }

  function surveyRaceLabel(value) {
    const scheme = [...rows("history"), ...rows("forecasts")].find(row => String(row.race_group) === String(value))?.race_scheme;
    if (scheme === "ces_hispanic_exclusive") {
      const labels = {white:"Non-Hispanic White",black:"Non-Hispanic Black",asian:"Non-Hispanic Asian",hispanic:"Hispanic (any race)",american_indian_alaska_native:"Non-Hispanic American Indian / Alaska Native",native_hawaiian_pacific_islander:"Non-Hispanic Native Hawaiian / Pacific Islander",other_race:"Other non-Hispanic race",multiracial:"Non-Hispanic multiracial",middle_eastern:"Non-Hispanic Middle Eastern"};
      return labels[value] || groupLabel("race_group",value);
    }
    return groupLabel("race_group",value);
  }

  function populateGroups() {
    const preferred = rows("history").find(row => finite(row.dem_share) || finite(row.national_dem_share)) || rows("forecasts")[0] || rows("demographics")[0];
    [["group-race","race_group"],["group-sex","sex"],["group-education","education"]].forEach(([id,field]) => {
      const current = $(id).value;
      const surveyRows = [...rows("history"), ...rows("forecasts")];
      const available = new Set(surveyRows.map(row => String(row[field])));
      const options = groupOptions(field).filter(option => !available.size || available.has(option.value));
      if (field === "race_group") options.forEach(option => { option.label = surveyRaceLabel(option.value); });
      $(id).innerHTML = options.length ? options.map(option => `<option value="${escape(option.value)}">${escape(option.label)}</option>`).join("") : '<option value="">No groups available</option>';
      if (options.some(option => option.value === current)) $(id).value = current;
      else if (preferred && options.some(option => option.value === String(preferred[field]))) $(id).value = String(preferred[field]);
      $(id).disabled = options.length === 0;
    });
  }

  function selectedRows(key) {
    return rows(key).filter(row => String(row.race_group ?? "") === $("group-race").value && String(row.sex ?? "") === $("group-sex").value && String(row.education ?? "") === $("group-education").value);
  }

  function renderPopulation() {
    const demographics = rows("demographics");
    const usable = demographics.filter(row => finite(row.population));
    show("population-content", usable.length > 0); show("population-empty", usable.length === 0);
    if (!usable.length) {
      $("population-empty").innerHTML = '<h4>Population data has not been loaded for this race</h4><p>Import ACS demographic counts for this geography to explore its race, sex, and education cells. Unavailable values are not treated as zero.</p>';
      $("acs-badge").textContent = "Awaiting population data"; return;
    }
    const aggregate = value => /^(all|total|all races|all sexes|both|both sexes|all education)$/i.test(String(value).replace(/_/g," "));
    const detailRows = usable.filter(row => !aggregate(row.race_group) && !aggregate(row.sex) && !aggregate(row.education));
    const counted = detailRows.length ? detailRows : usable;
    const total = counted.reduce((sum,row) => sum + row.population, 0);
    const byRace = new Map();
    counted.forEach(row => byRace.set(row.race_group, (byRace.get(row.race_group) || 0) + row.population));
    const groups = [...byRace].sort((a,b) => b[1]-a[1]);
    const colorMap = new Map(groups.map(([key],index) => [key,palette[index % palette.length]]));
    const years = [...new Set(usable.map(row => row.acs_year).filter(value => value != null))].sort();
    const vintages = [...new Set(usable.map(row => row.boundary_vintage).filter(Boolean))];
    $("population-total").textContent = fmt(total);
    $("acs-badge").textContent = `ACS ${years.length ? years.join(" / ") : "population data"}`;
    $("population-vintage").textContent = `${years.length ? `${years.join(" / ")} ACS` : "ACS estimates"}${vintages.length ? ` · Boundary vintage: ${vintages.join(", ")}` : ""}`;
    $("population-stack").innerHTML = groups.map(([key,count]) => `<span style="width:${total > 0 ? count/total*100 : 0}%;background:${colorMap.get(key)}" title="${escape(groupLabel("race_group",key))}: ${fmt(count)}"></span>`).join("");
    $("population-stack").setAttribute("aria-label", groups.map(([key,count]) => `${groupLabel("race_group",key)}: ${fmt(count)} residents`).join("; "));
    $("population-legend").innerHTML = groups.map(([key,count]) => `<span><i class="legend-swatch" style="background:${colorMap.get(key)}"></i>${escape(groupLabel("race_group",key))}<b>${pct(total > 0 ? count/total : null)}</b></span>`).join("");
    const sorted = [...demographics].sort((a,b) => String(a.race_group).localeCompare(String(b.race_group)) || String(a.sex).localeCompare(String(b.sex)) || String(a.education).localeCompare(String(b.education)));
    const displayed = state.expanded ? sorted : sorted.slice(0,8);
    $("demographic-table").innerHTML = displayed.map(row => `<tr><td><span class="table-swatch" style="background:${colorMap.get(row.race_group) || "#a1b192"}"></span>${escape(groupLabel("race_group",row.race_group))}</td><td>${escape(groupLabel("sex",row.sex))}</td><td>${escape(groupLabel("education",row.education))}</td><td class="numeric">${fmt(row.population)}</td><td class="numeric">${finite(row.moe) ? `±${fmt(row.moe)}` : "—"}</td></tr>`).join("");
    show("expand-demographics", sorted.length > 8);
    $("expand-demographics").innerHTML = state.expanded ? 'Show fewer cells <span aria-hidden="true">↑</span>' : `Show all ${fmt(sorted.length)} demographic cells <span aria-hidden="true">↓</span>`;
    $("expand-demographics").setAttribute("aria-expanded", String(state.expanded));
    const universes = [...new Set(usable.map(row => row.universe).filter(Boolean))];
    const harmonized=rows("forecasts").some(row=>row.race_scheme==="modeled_hispanic_exclusive");
    $("population-caveat").textContent = `Education estimates cover residents age 25 and older, including noncitizens; this is not the eligible electorate or expected turnout. The bar describes the loaded modeled cells.${harmonized?" Hispanic ethnicity is separated from non-Hispanic race groups using ACS totals and estimated allocations; residual non-White group counts are approximations.":" Sex and race categories follow ACS definitions; race is not the same as Hispanic ethnicity."}${universes.length ? ` Source universe: ${universes.join("; ")}.` : ""}`;
  }

  function renderHistory() {
    const history = selectedRows("history").sort((a,b) => Number(a.election_year)-Number(b.election_year));
    const ces = history.some(row => row.race_scheme === "ces_hispanic_exclusive") || selectedRows("forecasts").some(row => row.race_scheme === "ces_hispanic_exclusive");
    $("survey-definition").textContent = ces ? "CES survey definition: Hispanic respondents form a separate, any-race group; the other race groups exclude Hispanic respondents. These age-25+ survey categories differ from the ACS population categories above. National benchmarks include local respondents. Shares are two-party, with raw and effective samples below 30 suppressed." : "Compare the same source, office, race definition, and age universe. The national benchmark may include local respondents. Shares are two-party; estimates below publication thresholds stay missing.";
    const hasValues = history.some(row => finite(row.dem_share) || finite(row.national_dem_share));
    show("history-content", hasValues); show("history-empty", !hasValues);
    if (!hasValues) return;
    drawHistoryChart(history);
    const latest = history.filter(row => finite(row.dem_share)).at(-1) || history.at(-1);
    $("latest-local").textContent = pct(latest.dem_share);
    $("latest-year").textContent = `${latest.election_year} ${officeLabel(latest.office || state.detail.race?.office)}`;
    $("latest-national").textContent = pct(latest.national_dem_share);
    const gap = finite(latest.gap_pp) ? latest.gap_pp : finite(latest.dem_share) && finite(latest.national_dem_share) ? (latest.dem_share-latest.national_dem_share)*100 : null;
    $("latest-gap").textContent = pp(gap);
    $("history-table").innerHTML = history.map(row => `<tr><td>${escape(row.election_year)} ${escape(officeLabel(row.office || state.detail.race?.office))}</td><td class="numeric">${pct(row.dem_share)}</td><td class="numeric">${pct(row.national_dem_share)}</td><td class="numeric">${fmt(row.sample_size)}</td><td title="${escape(row.estimate_kind || "")}">${escape(row.source_name || titleCase(row.estimate_kind) || "Source not recorded")}</td></tr>`).join("");
  }

  function drawHistoryChart(history) {
    const width=650, height=225, left=42, right=22, top=18, bottom=34;
    const years=[...new Set(history.map(row=>Number(row.election_year)))].filter(Number.isFinite).sort((a,b)=>a-b);
    const minYear=Math.min(...years), maxYear=Math.max(...years);
    const x=year=>left+(maxYear===minYear ? (width-left-right)/2 : (year-minYear)/(maxYear-minYear)*(width-left-right));
    const y=value=>top+(1-value)*(height-top-bottom);
    let svg=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Democratic vote share by election year, selected constituency versus the same group nationally"><title>Historical Democratic vote share</title>`;
    [0,.25,.5,.75,1].forEach(value=>{svg+=`<line x1="${left}" y1="${y(value)}" x2="${width-right}" y2="${y(value)}" stroke="#e9ede4" ${value===.5 ? 'stroke-dasharray="4 4"' : ""}/><text x="${left-9}" y="${y(value)+4}" text-anchor="end" fill="#7b8974" font-family="Arial,sans-serif" font-size="12">${Math.round(value*100)}%</text>`;});
    years.forEach(year=>{svg+=`<text x="${x(year)}" y="${height-10}" text-anchor="middle" fill="#78866f" font-family="Arial,sans-serif" font-size="12">${year}</text>`;});
    [["national_dem_share","#c79553",true],["dem_share","#1b6960",false]].forEach(([field,color,dashed])=>{
      let segment=[];
      const flush=()=>{if(segment.length>1)svg+=`<polyline points="${segment.join(" ")}" fill="none" stroke="${color}" stroke-width="2.2" ${dashed?'stroke-dasharray="5 5"':""}/>`;segment=[];};
      history.forEach(row=>{if(finite(row[field]) && Number.isFinite(Number(row.election_year)))segment.push(`${x(Number(row.election_year))},${y(row[field])}`);else flush();});flush();
      history.filter(row=>finite(row[field])).forEach(row=>{svg+=`<circle cx="${x(Number(row.election_year))}" cy="${y(row[field])}" r="3.5" fill="${dashed ? "#fff" : color}" stroke="${color}" stroke-width="1.7"><title>${escape(row.election_year)} ${field==="dem_share"?"local":"national"}: ${pct(row[field])}</title></circle>`;});
    });
    $("history-chart").innerHTML=svg+"</svg>";
  }

  function renderProjection() {
    const projection=state.detail.projection || {};
    state.scenario=projection;
    const forecasts=selectedRows("forecasts");
    const selected=forecasts[0];
    const modelStatus=String(selected?.model_status || projection.status || "unavailable").toLowerCase();
    const calibrated=["calibrated","validated","calibrated_forecast"].includes(modelStatus);
    const experimental=/experimental|forecast/.test(modelStatus) || finite(projection.dem_share);
    $("model-status").textContent=calibrated ? "Calibrated model" : experimental ? "Experimental model" : /scenario/.test(modelStatus) ? "Scenario only" : /baseline/.test(modelStatus) ? "Baseline only" : "Unavailable";
    $("model-status").classList.toggle("calibrated",calibrated);
    $("projection-message").textContent=projection.message || "The model shifts 2024 demographic voting baselines by the national environment and group-specific news, then combines the groups by expected midterm voters.";
    const projected=selected?.projected_dem_share ?? projection.dem_share;
    show("forecast-output",(calibrated || experimental) && finite(projected));
    if((calibrated || experimental) && finite(projected)){
      const lower=selected?.lower ?? projection.lower, upper=selected?.upper ?? projection.upper;
      $("forecast-output").innerHTML=`<div class="forecast-estimate"><strong>${pct(projected)}</strong><span>2026 Democratic share for the selected combination<br>${selected ? `${escape(surveyRaceLabel(selected.race_group))} · ${escape(groupLabel("sex",selected.sex))} · ${escape(groupLabel("education",selected.education))}<br>2024 modeled baseline: ${pct(selected.baseline_dem_share)} · Change: ${pp(selected.swing_pp ?? (projected-selected.baseline_dem_share)*100)}` : "Population-weighted race estimate"}${finite(lower)&&finite(upper)?`<br>Reported interval: ${pct(lower)}–${pct(upper)}`:""}</span></div>`;
    }
    renderRaceForecast(projection);
    renderForecastCohorts();
    const historical=selectedRows("history").filter(row=>finite(row.dem_share)).sort((a,b)=>Number(a.election_year)-Number(b.election_year)).at(-1);
    state.baseline=finite(selected?.baseline_dem_share) ? {value:selected.baseline_dem_share,label:`${selected.baseline_geography === "us" ? "National" : "Local"} ${projection.baseline_year || "historical"} survey baseline`,geography:selected.baseline_geography} : historical ? {value:historical.dem_share,label:`${historical.election_year} local survey estimate`,geography:historical.geography_id} : null;
    $("scenario-shift").disabled=!state.baseline;
    const feature=projection.news_feature || {};
    $("news-feature").textContent=finite(feature.difference) ? `30-day net partisan news signal = ${feature.difference.toFixed(3)} · ${fmt(feature.article_count)} observations · ${fmt(feature.publisher_count)} publishers. Assumed news shift = this signal × your coefficient.` : "A news-based shift needs party-attributed observations in the 30-day window. An additional manual shift can still be explored where a baseline exists.";
    const drivers=Array.isArray(projection.drivers)?projection.drivers:[];
    const env=projection.national_environment || {};
    const margin=share=>finite(share)?`${share>=.5?"D":"R"} +${Math.abs(200*share-100).toFixed(1)}`:"—";
    $("news-signal-summary").innerHTML=`<h4>What moves this forecast from 2024</h4><div class="news-signal-stats"><span>Baseline (House vote)<strong>${margin(env.target_two_party_d)}</strong></span><span>Final, with turnout &amp; news<strong>${margin(env.final_two_party_d)}</strong></span><span>Typical midterm gain<strong>${margin(env.prior_two_party_d)}</strong></span><span>Generic-ballot polls<strong>${margin(env.poll_two_party_d)}</strong></span></div><p>The national baseline averages the typical out-party midterm gain since 2010 with the generic-ballot polling average. Every group moves toward it in proportion to how strongly that group swung in the 2018 midterm (CES survey). Each group is weighted by its validated midterm turnout, and news coverage of a specific racial group then moves that group on top of the baseline, by a coefficient fitted to the 2010–2022 midterms.</p>${drivers.length?`<div class="news-driver-list">${drivers.map(driver=>`<span>${escape(titleCase(driver.issue))} (margin)<b>${pp(2*driver.contribution_pp)}</b></span>`).join("")}</div>`:""}${renderGroupSignals(feature, state.summary?.model?.news_response)}`;
    renderScenario();
    if (Number($("news-coefficient").value) || Number($("scenario-shift").value)) requestScenario();
  }

  const groupNames={hispanic:"Hispanic / Latino",black:"Black",asian:"Asian American",white:"White",native:"Native American"};
  function renderGroupSignals(feature, response) {
    const groups=feature.groups || {};
    if(!Object.keys(groups).length)return "";
    const coefficient=finite(response?.coefficient_pp_per_unit)?response.coefficient_pp_per_unit:null;
    const rows=Object.entries(groupNames).map(([key,label])=>{
      const item=groups[key] || {};
      const strength=(item.articles || 0)/((item.articles || 0)+8);
      // The same uncentered shift the forecast adds to this group.
      const effect=finite(item.signal)&&finite(coefficient)?coefficient*item.signal*strength:null;
      const headlines=(item.top_articles || []).slice(0,3).map(a=>`<li><b class="${a.direction>=0?"swing-dem":"swing-rep"}">${a.direction>=0?"D":"R"}</b> ${escape(a.title)} <small>${escape(a.source)}${a.kind==="incumbent_referendum"?" · policy coverage, governing-party rule":""}</small></li>`).join("");
      return `<tr><td>${escape(label)}</td><td class="numeric">${finite(item.signal)?`${item.signal>0?"+":""}${item.signal.toFixed(3)}`:"—"}</td><td class="numeric">${fmt(item.articles || 0)}<small>${fmt(item.relevant_articles || 0)} relevant</small></td><td class="numeric ${effect>=0?"swing-dem":"swing-rep"}">${finite(effect)?pp(effect):"—"}</td></tr>${headlines?`<tr class="group-headlines"><td colspan="4"><ul>${headlines}</ul></td></tr>`:""}`;
    }).join("");
    return `<div class="group-signals"><h4>Racial-group news signals</h4><p>Group signal = party direction of coverage about that group or its issues (immigration enforcement, voting rights, policing…). Shift = ${finite(coefficient)?`${coefficient.toFixed(2)}${finite(response?.coefficient_se)?` ± ${response.coefficient_se.toFixed(2)}`:""}`:"fitted"} pts × group signal × evidence strength, added on top of the national baseline. The coefficient is fitted to 2010–2022 midterm coverage and CES group swings and is imprecise.</p><div class="data-table-wrap"><table><thead><tr><th>Group</th><th class="numeric">Group signal</th><th class="numeric">Scored articles</th><th class="numeric">Shift for group</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
  }

  function renderRaceForecast(projection) {
    const race=state.detail.race || {};
    const aggregate=projection.race_prediction || projection;
    const share=aggregate.dem_share ?? aggregate.projected_dem_share;
    show("race-forecast-card",finite(share));
    if(!finite(share))return;
    const margin=(share-.5)*200, swing=aggregate.swing_pp ?? (finite(aggregate.baseline_dem_share)?(share-aggregate.baseline_dem_share)*100:null);
    const winner=margin>=0 ? "Democratic" : "Republican";
    const color=margin>=0?"#1f4e87":"#9d3346";
    const coverage=aggregate.news_article_count ?? aggregate.news_feature?.article_count ?? projection.news_feature?.article_count;
    $("race-forecast-card").innerHTML=`<div class="race-forecast-top"><div><p class="eyebrow">2026 TURNOUT-WEIGHTED FORECAST</p><strong style="color:${color}">${winner} +${Math.abs(margin).toFixed(1)}</strong><span>Projected two-party margin · experimental model</span>${finite(aggregate.win_probability_d)?`<span class="win-odds">Win probability: <b style="color:#1c408c">D ${winPct(aggregate.win_probability_d)}</b> · <b style="color:#bf1d29">R ${winPct(1-aggregate.win_probability_d)}</b> · ${escape(ratingLabel(aggregate.rating))}</span>`:""}</div><a href="#outlook">Inspect the demographic model ↓</a></div><div class="race-share-labels"><span>Democratic <b>${pct(share)}</b></span><span>Republican <b>${pct(1-share)}</b></span></div><div class="race-share-bar"><span style="width:${share*100}%"></span></div><div class="race-forecast-facts"><span>2024 baseline D share <b>${pct(aggregate.baseline_dem_share)}</b></span><span>National environment <b>${pp(aggregate.environment_shift_pp)}</b></span><span>Midterm turnout <b>${pp(aggregate.turnout_shift_pp)}</b></span><span>Group news <b>${pp(aggregate.news_margin_shift_pp)}</b></span></div><p class="race-forecast-note">Shifts are in margin points. Cohorts are weighted by population × validated turnout for their race, sex and education. Residents age 25+ are the population base; noncitizens are not removed.</p>`;
  }

  function renderForecastCohorts() {
    const cohorts=rows("forecasts").filter(row=>finite(row.projected_dem_share));
    show("forecast-cohorts",cohorts.length>0);
    if(!cohorts.length)return;
    const total=cohorts.reduce((sum,row)=>sum+(row.population || 0),0);
    const ranked=cohorts.slice().sort((a,b)=>(b.population || 0)-(a.population || 0));
    $("forecast-cohort-table").innerHTML=ranked.map(row=>{
      const swing=row.swing_pp ?? (row.projected_dem_share-row.baseline_dem_share)*100;
      const weight=row.weight ?? row.population_weight ?? (total>0?(row.population || 0)/total:null);
      return `<tr><td><span class="cohort-name">${escape(surveyRaceLabel(row.race_group))} · ${escape(groupLabel("sex",row.sex))}</span><small>${escape(groupLabel("education",row.education))}</small></td><td class="numeric">${pct(weight)}<small>${fmt(row.population)} residents</small></td><td class="numeric">${pct(row.baseline_dem_share)}</td><td class="numeric"><strong>${pct(row.projected_dem_share)}</strong></td><td class="numeric ${swing>=0?"swing-dem":"swing-rep"}">${pp(swing)}</td></tr>`;
    }).join("");
    $("forecast-weight-note").textContent=`${fmt(cohorts.length)} demographic combinations · ${fmt(total)} modeled residents. Education is grouped into high school or less and some college or more. The 2024 shares are fitted survey baselines, not observed district results. Race/ethnicity harmonization and population weighting follow the model documentation; cohort shifts are assumed responses to news, not measured causal effects.`;
  }

  function renderScenario() {
    const shift=Number($("scenario-shift").value);
    const coefficient=Number($("news-coefficient").value);
    $("scenario-shift-label").textContent=pp(shift);
    $("news-coefficient-label").textContent=`${pp(coefficient)} / unit`;
    if(!state.baseline){$("scenario-result").textContent="A documented survey baseline is needed before this scenario can be calculated.";return;}
    const feature=state.scenario?.news_feature || state.detail.projection?.news_feature || {};
    if(coefficient && !finite(feature.difference)) {$("scenario-result").textContent="This news assumption cannot be calculated: party-attributed news tone is missing.";return;}
    const newsShift=coefficient*(feature.difference || 0);
    const value=Math.max(0,Math.min(1,state.baseline.value+(shift+newsShift)/100));
    $("scenario-result").innerHTML=`Assumed Democratic share <strong>${pct(value)}</strong><br><span style="font-family:var(--sans);font-size:12px">${escape(state.baseline.label)}: ${pct(state.baseline.value)} · News assumption: ${pp(newsShift)} · Additional shift: ${pp(shift)}. ${state.baseline.geography === "us" ? "National reference only; this is not an observed local baseline." : "Local historical survey baseline."}</span>`;
  }

  function requestScenario() {
    renderScenario();
    // No server on a static host; keep the in-browser estimate from renderScenario.
    if (STATIC) return;
    window.clearTimeout(state.scenarioTimer);
    const request=++state.scenarioRequest, raceId=state.selectedId;
    state.scenarioTimer=window.setTimeout(async()=>{
      const params=new URLSearchParams({id:raceId,as_of:state.summary?.as_of || state.detail.projection?.as_of || "2026-10-21",coefficient_pp:$("news-coefficient").value,assumed_shift_pp:$("scenario-shift").value});
      try {
        const response=await api(`/api/scenario?${params}`);
        if(request!==state.scenarioRequest || raceId!==state.selectedId)return;
        state.scenario=response;
        const group=(response.groups || []).find(row=>String(row.race_group)===$("group-race").value && String(row.sex)===$("group-sex").value && String(row.education)===$("group-education").value);
        if(group && finite(group.projected_dem_share)) {
          $("scenario-result").innerHTML=`Assumed Democratic share <strong>${pct(group.projected_dem_share)}</strong><br><span style="font-family:var(--sans);font-size:12px">${group.baseline_geography === "us" ? "National" : "Local"} ${escape(response.baseline_year)} survey baseline: ${pct(group.baseline_dem_share)} · Total assumed change: ${pp(group.swing_pp)}. ${group.baseline_geography === "us" ? "National reference only; not an observed local baseline." : "Local historical survey baseline."}<br>${escape(group.exposure_note || "Uniform assumed shift; no demographic-specific response has been measured.")}</span>`;
        } else $("scenario-result").textContent=response.status === "missing_sentiment" ? response.message : "No usable survey baseline for the selected demographic combination in this scenario run. Choose another combination; no estimate is invented.";
        announce("News sensitivity scenario updated. This is not a calibrated forecast.");
      } catch(error) {
        if(request===state.scenarioRequest)$("scenario-result").textContent=`The scenario service could not be reached. ${error.message}`;
      }
    },250);
  }

  function renderNews() {
    const news=rows("news").slice().sort((a,b)=>String(b.published_at).localeCompare(String(a.published_at)));
    const scored=news.filter(item=>finite(item.compound ?? item.sentiment));
    $("news-count").textContent=`${fmt(scored.length)} / ${fmt(news.length)} scored`;
    $("news-list").innerHTML=news.length ? news.map(item=>{
      const url=safeUrl(item.url);
      const score=item.compound ?? item.sentiment;
      const sentiment=finite(score) ? `${score===0?"Neutral ":score>0?"+":""}${score.toFixed(3)}` : "Unscored";
      const topics=Array.isArray(item.topics)?item.topics.map(topic=>typeof topic==="string"?titleCase(topic):topic.label || titleCase(topic.topic || topic.name)).filter(Boolean):item.topics&&typeof item.topics==="object"?Object.entries(item.topics).filter(([,salience])=>finite(salience)&&salience>0).sort((a,b)=>b[1]-a[1]).map(([topic])=>titleCase(topic)):[];
      const description=String(item.content || item.description || item.summary || "").replace(/<[^>]*>/g," ").slice(0,260);
      const tone=finite(item.dem_tone)||finite(item.rep_tone)?`<div class="party-tones">${finite(item.dem_tone)?`<span>D tone <b>${item.dem_tone.toFixed(3)}</b></span>`:""}${finite(item.rep_tone)?`<span>R tone <b>${item.rep_tone.toFixed(3)}</b></span>`:""}</div>`:"";
      if(item.analysis_method)item.method=item.analysis_method;
      return `<article class="news-item"><div class="news-item-copy">${url?`<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(item.title)} ↗</a>`:`<span>${escape(item.title)}</span>`}<div class="news-meta">${escape(item.source_name || "Source not recorded")} · ${escape(date(item.published_at))}${item.party?` · Referenced party: ${escape(item.party)}`:""}</div>${description?`<p class="news-description">${escape(description)}</p>`:""}${topics.length?`<div class="news-topics">${topics.map(topic=>`<span>${escape(topic)}</span>`).join("")}</div>`:""}${tone}${item.explanation?`<p class="news-explanation">${escape(item.explanation)}</p>`:""}${item.coverage_status==="no_lexicon_matches"?'<p class="news-explanation">No sentiment terms matched; zero is a neutral fallback. Party tone is unavailable unless a party was attributed.</p>':""}</div><span class="sentiment-chip ${!finite(score)?"unscored":Math.abs(score)<.05?"neutral":score>0?"positive":"negative"}" title="${escape(item.analysis_method || item.method || "Compound tone from headline and available description, on a −1 to +1 scale. A score of zero is neutral; unscored means missing.")}">${escape(sentiment)}</span></article>`;
    }).join("") : '<div class="news-empty">No sourced news observations have been imported for this race. Sentiment scores will appear here when evidence is available.</div>';
  }

  const tilePositions={ME:[11,0],WA:[0,1],ID:[1,1],MT:[2,1],ND:[3,1],MN:[4,1],WI:[5,1],MI:[7,1],NY:[9,1],VT:[10,1],NH:[11,1],OR:[0,2],NV:[1,2],WY:[2,2],SD:[3,2],IA:[4,2],IL:[5,2],IN:[6,2],OH:[7,2],PA:[8,2],NJ:[9,2],MA:[10,2],CA:[0,3],AZ:[1,3],UT:[2,3],NE:[3,3],MO:[4,3],KY:[5,3],WV:[6,3],VA:[7,3],MD:[8,3],DE:[9,3],CT:[10,3],RI:[11,3],HI:[0,4],NM:[1,4],CO:[2,4],KS:[3,4],AR:[4,4],TN:[5,4],NC:[6,4],SC:[7,4],AK:[0,5],OK:[3,5],LA:[4,5],MS:[5,5],AL:[6,5],GA:[7,5],TX:[3,6],FL:[8,6]};
  const stateCenters={AL:[-86.8,32.8],AK:[-152.5,64],AZ:[-111.9,34.3],AR:[-92.4,34.9],CA:[-119.4,36.8],CO:[-105.5,39],CT:[-72.7,41.6],DE:[-75.4,39],FL:[-82,28.5],GA:[-83.4,32.7],HI:[-157.6,20.9],ID:[-114.5,44.2],IL:[-89.2,40],IN:[-86.2,40],IA:[-93.5,42],KS:[-98.4,38.5],KY:[-85.3,37.7],LA:[-91.8,31],ME:[-69.3,45.2],MD:[-76.8,39.2],MA:[-71.8,42.3],MI:[-85.5,44.2],MN:[-94.6,46.1],MS:[-89.7,32.7],MO:[-92.5,38.5],MT:[-110.4,46.8],NE:[-99.9,41.6],NV:[-117,39.3],NH:[-71.6,43.8],NJ:[-74.5,40.2],NM:[-106.1,34.4],NY:[-75.5,43],NC:[-79.6,35.5],ND:[-100.4,47.5],OH:[-82.8,40.4],OK:[-97.5,35.5],OR:[-120.5,44],PA:[-77.8,40.9],RI:[-71.5,41.6],SC:[-80.9,33.8],SD:[-100.2,44.4],TN:[-86.3,35.9],TX:[-99.2,31.2],UT:[-111.7,39.3],VT:[-72.7,44.2],VA:[-78.5,37.7],WA:[-120.7,47.4],WV:[-80.6,38.6],WI:[-89.9,44.6],WY:[-107.6,43]};

  function predictionMargin(race) { return finite(race.margin_pp)?race.margin_pp:finite(race.dem_share)?(race.dem_share-.5)*200:null; }
  function predictionColor(race) {
    const margin=predictionMargin(race || {});
    if(!finite(margin))return marginColors.unavailable;
    if(race.rating && marginColors[race.rating])return marginColors[race.rating];
    if(Math.abs(margin)<2)return marginColors.tossup;
    if(margin>0)return marginColors[margin>=10?"safe_d":margin>=5?"likely_d":"lean_d"];
    return marginColors[margin<=-10?"safe_r":margin<=-5?"likely_r":"lean_r"];
  }
  function predictionDescription(race) {
    const margin=predictionMargin(race);
    if(!finite(margin))return "Estimate unavailable";
    const favorite=finite(race.win_probability_d)?(race.win_probability_d>=.5?`D ${winPct(race.win_probability_d)} to win`:`R ${winPct(1-race.win_probability_d)} to win`):"";
    return `${margin>=0?"D":"R"} +${Math.abs(margin).toFixed(1)}${favorite?` · ${favorite}`:""}`;
  }

  async function loadPredictions(office) {
    state.mapOffice=office;
    document.querySelectorAll("[data-map-office]").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.mapOffice===office)));
    hideMapTooltip();
    const request=++state.mapRequest;
    show("map-loading",true);show("house-map",false);show("senate-map",false);
    $("map-loading").textContent="Loading race projections…";
    try {
      if(!state.predictions[office])state.predictions[office]=await api(`/api/predictions?office=${office}`);
      if(office==="senate"&&!state.geography)state.geography=await api("/maps/us-states.json");
      if(request!==state.mapRequest)return;
      renderSeatMap();
    } catch(error) {
      if(request!==state.mapRequest)return;
      $("map-loading").innerHTML=`<span>Seat projections could not be loaded. ${escape(error.message)}</span><button type="button" class="map-retry">Try again</button>`;
      $("map-loading").querySelector("button").addEventListener("click",()=>loadPredictions(office));
    }
  }

  function renderSeatMap() {
    const payload=state.predictions[state.mapOffice] || {};
    const races=Array.isArray(payload.races)?payload.races:[];
    const projected=races.filter(race=>finite(race.dem_share));
    const dem=projected.filter(race=>race.dem_share>=.5).length, rep=projected.length-dem;
    const missing=races.length-projected.length;
    const senate=state.mapOffice==="senate";
    const hold=senate?(payload.holdovers || {D:0,R:0}):{D:0,R:0};
    // An older data service omits holdovers; never present 35 contests as the chamber.
    if(senate&&!payload.holdovers)notice("The running data service predates this page and did not send the 65 Senate seats not up in 2026. Stop it and run: python main.py serve");
    const total=senate?100:races.length || 435;
    $("map-dem-count").textContent=fmt(hold.D+dem);$("map-rep-count").textContent=fmt(hold.R+rep);
    $("map-dem-detail").textContent=senate?`${fmt(hold.D)} not up + ${fmt(dem)} projected`:"projected seats";
    $("map-rep-detail").textContent=senate?`${fmt(hold.R)} not up + ${fmt(rep)} projected`:"projected seats";
    $("map-seat-context").textContent=senate?"100 seats · 50 + VP tiebreak (R) or 51 for control":"435 seats · 218 for a majority";
    $("map-coverage").textContent=senate?`${fmt(races.length)} seats up in 2026 · ${fmt(hold.D+hold.R)} not up (D ${fmt(hold.D)} incl. 2 independents, R ${fmt(hold.R)})`:`${fmt(projected.length)} projected${missing?` · ${fmt(missing)} unavailable`:""} · counts follow each seat's favorite`;
    const chamber=payload.chamber || {};
    $("map-control").textContent=finite(chamber.p_dem_control)?`Chance of control: D ${winPct(chamber.p_dem_control)} · R ${winPct(chamber.p_rep_control)} (${fmt(chamber.simulations)} simulations)`:"";
    // 270toWin-style bar: holdover seats at the ends, then contested seats by rating tier.
    const counts=Object.fromEntries(ratingOrder.map(rating=>[rating,0]));
    projected.forEach(race=>{const rating=ratingOrder.includes(race.rating)?race.rating:(race.dem_share>=.5?"tilt_d":"tilt_r");counts[rating]++;});
    const segments=[["holdover_d",hold.D,"Democrats not up in 2026"],...ratingOrder.slice(0,4).map(r=>[r,counts[r],ratingLabel(r)]),["tossup",counts.tossup,"Toss-up"],["unavailable",missing,"No estimate"],...ratingOrder.slice(5).map(r=>[r,counts[r],ratingLabel(r)]),["holdover_r",hold.R,"Republicans not up in 2026"]];
    const shown=segments.filter(([,count])=>count>0);
    $("chamber-bar").innerHTML=shown.map(([key,count,label])=>`<span style="width:${count/total*100}%;background:${marginColors[key]}" title="${escape(label)}: ${fmt(count)}">${count/total>.035?fmt(count):""}</span>`).join("");
    $("chamber-bar").setAttribute("aria-label",shown.map(([,count,label])=>`${label}: ${count}`).join("; "));
    $("majority-marker").style.left=`${(senate?50:218)/total*100}%`;$("majority-label").textContent=senate?"50":"218";
    $("map-updated").textContent=payload.as_of?`As of ${date(payload.as_of)}`:"";
    $("map-geography-note").textContent=state.mapOffice==="house"?"House seat cartogram: one tile per district, grouped approximately geographically by state. Tiles do not represent district boundaries.":"State outlines: US Census Bureau, 2024. Alaska and Hawaii are insets at different scales. Gray states have no 2026 Senate election; each race is listed below.";
    if(state.mapOffice==="house")renderHouseMap(races);else renderSenateMap(races);
    show("map-loading",false);show("house-map",state.mapOffice==="house");show("senate-map",state.mapOffice==="senate");
    renderCompetitiveRaces(races);updateMapSelection();
  }

  function renderHouseMap(races) {
    const byState=new Map();
    races.forEach(race=>{const code=raceState(race);if(!byState.has(code))byState.set(code,[]);byState.get(code).push(race);});
    const grid=document.createElement("div");grid.className="house-state-grid";
    [...byState].forEach(([code,seats])=>{
      seats.sort((a,b)=>Number(a.district ?? String(a.race_id).split("-").at(-1))-Number(b.district ?? String(b.race_id).split("-").at(-1)));
      const card=document.createElement("div");card.className="house-state";
      const position=tilePositions[code] || [11,6];card.style.gridColumn=position[0]+1;card.style.gridRow=position[1]+1;
      card.innerHTML=`<div class="house-state-label"><span>${escape(code)}</span><small>${seats.length}</small></div>`;
      const tiles=document.createElement("div");tiles.className="district-tiles";
      seats.forEach(race=>{
        const button=document.createElement("button");button.type="button";button.className="district-tile";button.style.background=predictionColor(race);button.dataset.mapRace=race.race_id;
        button.setAttribute("aria-label",`${race.label}: ${predictionDescription(race)}. Select race.`);
        attachMapInteraction(button,[race]);tiles.append(button);
      });
      card.append(tiles);grid.append(card);
    });
    $("house-map").replaceChildren(grid);
  }

  function albers([longitude,latitude]) {
    const radians=Math.PI/180, phi=latitude*radians, n=(Math.sin(29.5*radians)+Math.sin(45.5*radians))/2;
    const c=Math.cos(29.5*radians)**2+2*n*Math.sin(29.5*radians), rho=Math.sqrt(c-2*n*Math.sin(phi))/n;
    const rho0=Math.sqrt(c-2*n*Math.sin(37.5*radians))/n, theta=n*(longitude+96)*radians;
    return [rho*Math.sin(theta),rho0-rho*Math.cos(theta)];
  }

  function geometryRings(feature){return feature.geometry.type==="MultiPolygon"?feature.geometry.coordinates.flat():feature.geometry.coordinates;}

  function renderSenateMap(races) {
    const namespace="http://www.w3.org/2000/svg",svg=document.createElementNS(namespace,"svg");
    svg.setAttribute("viewBox","0 0 1000 640");svg.setAttribute("role","group");svg.setAttribute("aria-label","2026 Senate race projections by state");
    const features=state.geography?.features || [];
    const continental=features.filter(feature=>!["AK","HI"].includes(feature.properties.state)).flatMap(feature=>geometryRings(feature).flatMap(ring=>ring.map(albers)));
    const xs=continental.map(point=>point[0]),ys=continental.map(point=>point[1]),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys);
    const scale=Math.min(900/(xmax-xmin),540/(ymax-ymin));
    const project=(point,code)=>{
      if(code==="AK"){let longitude=point[0];if(longitude>0)longitude-=360;return [35+(longitude+188)/60*200,535-(point[1]-51)/21*125];}
      if(code==="HI")return [275+(point[0]+160.6)/7*120,550-(point[1]-18.7)/4*75];
      const [x,y]=albers(point);return [50+(x-xmin)*scale,35+(ymax-y)*scale];
    };
    const byState=new Map();races.forEach(race=>{const code=raceState(race);if(!byState.has(code))byState.set(code,[]);byState.get(code).push(race);});
    const callouts={MA:190,RI:220,CT:250,NJ:295,DE:325,MD:355};
    features.forEach(feature=>{
      const code=feature.properties.state,contests=byState.get(code) || [];
      const closest=contests.slice().sort((a,b)=>Math.abs(predictionMargin(a) || 0)-Math.abs(predictionMargin(b) || 0))[0];
      const path=document.createElementNS(namespace,"path");
      path.setAttribute("d",geometryRings(feature).map(ring=>ring.map((point,index)=>`${index?"L":"M"}${project(point,code).map(value=>value.toFixed(2)).join(",")}`).join("")+"Z").join(""));
      path.setAttribute("fill",predictionColor(closest));path.setAttribute("fill-rule","evenodd");path.setAttribute("class",`state-outline${contests.length?" has-election":""}`);
      if(contests.length){path.setAttribute("role","button");path.setAttribute("tabindex","0");path.dataset.mapRace=closest.race_id;path.setAttribute("aria-label",contests.map(race=>`${race.label}: ${predictionDescription(race)}`).join("; "));attachMapInteraction(path,contests);}
      const title=document.createElementNS(namespace,"title");title.textContent=`${feature.properties.name}${contests.length?": "+contests.map(predictionDescription).join("; "):" — no Senate election in 2026"}`;path.append(title);svg.append(path);
      if(stateCenters[code]){
        const [x,y]=project(stateCenters[code],code),label=document.createElementNS(namespace,"text");
        if(callouts[code]){
          const line=document.createElementNS(namespace,"path");line.setAttribute("d",`M${x.toFixed(2)},${y.toFixed(2)}L950,${callouts[code]}L964,${callouts[code]}`);line.setAttribute("class","state-label-leader");svg.append(line);
        }
        label.setAttribute("x",callouts[code]?979:x);label.setAttribute("y",callouts[code] || y);label.setAttribute("class",`state-map-label${contests.length?" has-election":""}`);label.textContent=code;svg.append(label);
      }
    });
    const insetLabel=document.createElementNS(namespace,"text");insetLabel.setAttribute("x","35");insetLabel.setAttribute("y","580");insetLabel.setAttribute("class","inset-caption");insetLabel.textContent="AK / HI · not to scale";svg.append(insetLabel);
    const listing=document.createElement("div");listing.className="senate-seat-list";
    races.slice().sort((a,b)=>raceState(a).localeCompare(raceState(b))).forEach(race=>{
      const button=document.createElement("button");button.type="button";button.className="senate-seat";button.dataset.mapRace=race.race_id;
      button.innerHTML=`<i style="background:${predictionColor(race)}"></i><span>${escape(raceState(race))}${race.election_type==="special"?" special":""}</span><b>${finite(predictionMargin(race))?`${predictionMargin(race)>=0?"D":"R"} +${Math.abs(predictionMargin(race)).toFixed(1)}`:"—"}</b>`;
      attachMapInteraction(button,[race]);listing.append(button);
    });
    $("senate-map").replaceChildren(svg,listing);
  }

  function attachMapInteraction(element,races) {
    element.addEventListener("pointerenter",event=>showMapTooltip(event,races));element.addEventListener("pointermove",event=>positionMapTooltip(event.clientX,event.clientY));element.addEventListener("pointerleave",hideMapTooltip);
    element.addEventListener("focus",()=>{const bounds=element.getBoundingClientRect();showMapTooltip({clientX:bounds.left+bounds.width/2,clientY:bounds.top},races);});element.addEventListener("blur",hideMapTooltip);
    element.addEventListener("click",()=>selectPrediction(races[0]));
    if(element.tagName.toLowerCase()!=="button")element.addEventListener("keydown",event=>{if(event.key==="Enter" || event.key===" "){event.preventDefault();selectPrediction(races[0]);}});
  }
  function showMapTooltip(event,races) {
    $("map-tooltip").innerHTML=races.map(race=>`<strong>${escape(race.label)}</strong><span>${escape(ratingLabel(race.rating))} · ${escape(predictionDescription(race))}</span><small>D win ${winPct(race.win_probability_d)} · R win ${winPct(race.win_probability_r)}<br>2024 baseline ${finite(race.baseline_margin_pp)?`${race.baseline_margin_pp>=0?"D":"R"} +${Math.abs(race.baseline_margin_pp).toFixed(1)}`:"—"} · news shift ${pp(race.news_margin_shift_pp)}${race.district_redrawn_for_2026?"<br>District redrawn for 2026":""}</small>`).join('<hr>');
    show("map-tooltip",true);positionMapTooltip(event.clientX,event.clientY);
  }
  function positionMapTooltip(x,y) {
    const tooltip=$("map-tooltip"),bounds=tooltip.getBoundingClientRect();
    tooltip.style.left=`${Math.max(12,Math.min(x+14,window.innerWidth-bounds.width-12))}px`;
    tooltip.style.top=`${Math.max(12,y+bounds.height+26>window.innerHeight?y-bounds.height-12:y+18)}px`;
  }
  function hideMapTooltip(){show("map-tooltip",false);}
  function selectPrediction(prediction) {
    hideMapTooltip();
    const race=state.races.find(item=>String(item.race_id)===String(prediction.race_id));
    if(!race)return;
    $("map-race-preview").innerHTML=`<div><strong>${escape(prediction.label)}</strong><span>${escape(predictionDescription(prediction))} · Democratic share change ${pp(prediction.swing_pp)}</span></div><a href="#race-detail">View forecast &amp; news evidence ↓</a>`;
    selectRace(race);
    announce(`${race.label} selected. Forecast details are below the seat map.`);
  }
  function updateMapSelection(){document.querySelectorAll("[data-map-race]").forEach(element=>element.classList.toggle("selected-seat",String(element.dataset.mapRace)===String(state.selectedId)));}
  function renderCompetitiveRaces(races) {
    const closest=races.filter(race=>finite(predictionMargin(race))).sort((a,b)=>Math.abs(predictionMargin(a))-Math.abs(predictionMargin(b))).slice(0,12);
    $("competitive-count").textContent=`${closest.length} races`;
    $("competitive-list").replaceChildren();
    closest.forEach(race=>{
      const button=document.createElement("button");button.type="button";button.dataset.mapRace=race.race_id;button.innerHTML=`<i style="background:${predictionColor(race)}"></i><span>${escape(race.label)}</span><strong>${finite(predictionMargin(race))?`${predictionMargin(race)>=0?"D":"R"} +${Math.abs(predictionMargin(race)).toFixed(1)}`:"—"}</strong>`;button.addEventListener("click",()=>selectPrediction(race));$("competitive-list").append(button);
    });
  }

  async function loadTable(office) {
    state.table.office=office;
    document.querySelectorAll("[data-table-office]").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.tableOffice===office)));
    const request=++state.table.request;
    try {
      if(!state.predictions[office])state.predictions[office]=await api(`/api/predictions?office=${office}`);
      if(request===state.table.request)renderTable();
    } catch(error) {
      if(request===state.table.request)$("race-table-body").innerHTML=`<tr><td colspan="8">The race table could not be loaded. ${escape(error.message)}</td></tr>`;
    }
  }

  function renderTable() {
    const {office,sort,dir}=state.table, query=state.table.search.toLowerCase().trim();
    const races=(state.predictions[office]?.races || []).filter(race=>!query || `${race.label} ${race.state} ${stateNames[raceState(race)] || ""} ${ratingLabel(race.rating)}`.toLowerCase().includes(query));
    const value=race=>sort==="closeness"?Math.abs((race.win_probability_d ?? 0)-.5):sort==="rating"?ratingOrder.indexOf(race.rating):sort==="label"?String(race.label):race[sort];
    races.sort((a,b)=>{const x=value(a),y=value(b);return (typeof x==="string"?x.localeCompare(y):(x ?? -Infinity)-(y ?? -Infinity))*dir;});
    $("table-count").textContent=`${fmt(races.length)} ${office==="house"?"House":office==="senate"?"Senate":"governor"} races${sort==="closeness"?" · closest first":""}`;
    const margin=value=>finite(value)?`${value>=0?"D":"R"} +${Math.abs(value).toFixed(1)}`:"—";
    $("race-table-body").innerHTML=races.length?races.map(race=>`<tr data-race="${escape(race.race_id)}" tabindex="0"><td><span class="table-swatch" style="background:${predictionColor(race)}"></span>${escape(race.label)}${race.district_redrawn_for_2026?' <small class="redrawn" title="2026 district lines differ from 2024">redrawn</small>':""}</td><td><span class="rating-chip" style="background:${predictionColor(race)}">${escape(ratingLabel(race.rating))}</span></td><td class="numeric"><span class="prob-bar"><i style="width:${(race.win_probability_d || 0)*100}%"></i></span>${winPct(race.win_probability_d)}</td><td class="numeric">${winPct(race.win_probability_r)}</td><td class="numeric"><strong>${margin(race.margin_pp)}</strong></td><td class="numeric">${margin(race.baseline_margin_pp)}</td><td class="numeric">${pp(race.news_margin_shift_pp)}</td><td class="numeric">${winPct(race.win_probability_d_without_news)}</td></tr>`).join(""):'<tr><td colspan="8">No races match this filter.</td></tr>';
    document.querySelectorAll("[data-sort]").forEach(button=>button.setAttribute("aria-sort",button.dataset.sort===sort?(dir>0?"ascending":"descending"):"none"));
  }

  function openTableRace(id) {
    const race=state.races.find(item=>String(item.race_id)===String(id));
    if(race){selectRace(race);$("race-detail").scrollIntoView?.({behavior:"smooth"});}
  }

  // Submission picks (electiondb/kalshi.py): model odds weighed against Kalshi prices.
  const partyColor = party => party === "D" ? "#1c408c" : party === "R" ? "#bf1d29" : "#6b7477";
  const pickLabel = race => `<b class="pick-chip" style="background:${partyColor(race.pick)}">${escape(race.pick)}</b>${escape(race.candidate || (race.pick === "D" ? "Democrat" : "Republican"))}`;
  const points = value => finite(value) ? value.toFixed(2) : "—";
  const reasonLabel = {model_auto:"Model favorite (automatic)",favorite_value:"Favorite: better expected points",underdog_value:"Underdog: expected points ≥ threshold",third_candidate:"Favorite: strong third candidate",no_market:"Favorite: no Kalshi market"};

  function renderStrategy(data) {
    const rules=data.rules || {}, summary=data.summary || {}, races=Array.isArray(data.races)?data.races:[];
    const auto=Math.round((rules.auto_pick_above ?? .8)*100), ratio=rules.underdog_ratio ?? 2, third=Math.round((rules.third_candidate ?? .05)*100);
    $("picks-rules").innerHTML=[
      `<li><b>Clear favorites are automatic.</b> If my model gives a candidate more than ${auto}%, I pick that candidate.</li>`,
      `<li><b>Otherwise, compare expected points.</b> Expected points = model win probability × log<sub>½</sub>(Kalshi price). I pick the model's underdog only when its expected points are at least ${ratio}× the favorite's. Example: 30% in my model but 3% on Kalshi gives 0.30 × 5.06 = 1.52, against 0.70 × 0.04 = 0.03 for the favorite, so I take the underdog.</li>`,
      `<li><b>Safeguards.</b> With no Kalshi market, or when an independent or third candidate trades at ${third}% or more, I keep the favorite, because the model only splits the vote between Democrats and Republicans. Kalshi prices are the bid/ask midpoint, floored at ${Math.round((rules.price_floor ?? .01)*100)}¢.</li>`
    ].join("");
    const reasons=summary.reasons || {}, picks=summary.picks || {};
    const stat=(label,value,note)=>`<div><span>${label}</span><strong>${value}</strong><small>${note}</small></div>`;
    $("picks-stats").innerHTML=[
      stat("Races picked",fmt(summary.races),`House ${fmt((picks.house?.D ?? 0)+(picks.house?.R ?? 0))} · Senate ${fmt((picks.senate?.D ?? 0)+(picks.senate?.R ?? 0))}`),
      stat("Automatic picks",fmt(reasons.model_auto),`Model favorite above ${auto}%`),
      stat("Underdog picks",fmt(summary.underdog_picks),`Expected points ≥ ${ratio}× the favorite's`),
      stat("Expected points",finite(summary.expected_points)?summary.expected_points.toFixed(1):"—","If the model's odds are right"),
      stat("Picks by party",`${fmt((picks.house?.D ?? 0)+(picks.senate?.D ?? 0))} D · ${fmt((picks.house?.R ?? 0)+(picks.senate?.R ?? 0))} R`,`Senate ${fmt(picks.senate?.D)} D · ${fmt(picks.senate?.R)} R`)
    ].join("");
    $("picks-updated").textContent=data.kalshi_fetched_at?`Kalshi prices as of ${new Date(data.kalshi_fetched_at).toLocaleString("en-US",{month:"short",day:"numeric",hour:"numeric",minute:"2-digit",timeZoneName:"short"})}`:"Kalshi prices not fetched yet";
    const kalshiPick=race=>race.pick==="D"?race.kalshi_d:race.kalshi_r;
    const underdogs=races.filter(race=>race.reason==="underdog_value").sort((a,b)=>(b.expected_points ?? 0)-(a.expected_points ?? 0));
    $("picks-underdogs").innerHTML=underdogs.length?underdogs.map(race=>`<tr data-race="${escape(race.race_id)}" tabindex="0"><td>${escape(race.label)}</td><td>${pickLabel(race)}</td><td class="numeric">${winPct(race.model_probability)}</td><td class="numeric">${winPct(kalshiPick(race))}</td><td class="numeric">${points(race.points_if_right)}</td><td class="numeric"><strong>${points(race.expected_points)}</strong></td><td class="numeric">${points(race.ev_favorite)}</td></tr>`).join(""):'<tr><td colspan="7">No race meets the underdog threshold.</td></tr>';
    const close=races.filter(race=>race.reason!=="model_auto").sort((a,b)=>(b.underdog_ratio ?? -1)-(a.underdog_ratio ?? -1));
    $("picks-close-count").textContent=`${close.length} races`;
    $("picks-close").innerHTML=close.map(race=>`<tr data-race="${escape(race.race_id)}" tabindex="0"><td>${escape(race.label)}</td><td><b class="pick-chip" style="background:${partyColor(race.favorite)}">${escape(race.favorite)}</b></td><td class="numeric">${winPct(race.model_favorite_probability)}</td><td class="numeric">${winPct(race.kalshi_d)} / ${winPct(race.kalshi_r)}</td><td class="numeric">${points(race.ev_favorite)}</td><td class="numeric">${points(race.ev_underdog)}</td><td>${pickLabel(race)} <small>${escape(reasonLabel[race.reason] || race.reason)}</small></td></tr>`).join("");
    show("picks",true);
  }

  async function loadStrategy() {
    try { renderStrategy(await api("/api/strategy")); }
    catch { show("picks",false); }
  }

  async function initialize() {
    show("app-notice",false);
    loadStrategy();
    if(/senate/i.test(window.location?.hash || ""))state.mapOffice="senate";
    const mapPromise=loadPredictions(state.mapOffice);
    loadTable(state.table.office);
    const results=await Promise.allSettled([api("/api/summary"),api("/api/races"),api("/api/groups")]);
    if(results[0].status==="fulfilled")state.summary=results[0].value;
    if(results[1].status==="fulfilled")state.races=Array.isArray(results[1].value.races)?results[1].value.races:[];
    if(results[2].status==="fulfilled")state.groups=results[2].value;
    renderSummary();populateStates();renderRaceList();
    if(results.some(result=>result.status==="rejected"))notice("Some research data could not be loaded. Start the local data service, then retry.");
    if(state.races.length){
      const selected=state.races.find(race=>String(race.race_id)===String(state.selectedId)) || state.races.find(race=>raceState(race)==="NC"&&raceOffice(race)==="senate") || state.races.find(race=>raceOffice(race)==="senate") || state.races[0];
      await selectRace(selected);
    }else{
      $("detail-loading").textContent=results[1].status==="rejected" ? "The race directory could not be loaded. Start the local data service and use Retry above." : "No races are available yet. Import the 2026 race directory to begin.";
      $("race-detail").setAttribute("aria-busy","false");
    }
    await mapPromise;
  }

  $("race-search").addEventListener("input",event=>{state.search=event.target.value;renderRaceList();});
  $("state-filter").addEventListener("change",event=>{state.state=event.target.value;renderRaceList();});
  document.querySelectorAll("[data-office]").forEach(button=>button.addEventListener("click",()=>{
    state.office=button.dataset.office;
    document.querySelectorAll("[data-office]").forEach(item=>item.setAttribute("aria-pressed",String(item===button)));
    renderRaceList();
  }));
  ["group-race","group-sex","group-education"].forEach(id=>$(id).addEventListener("change",()=>{renderHistory();renderProjection();announce("Demographic selection updated.");}));
  $("expand-demographics").addEventListener("click",()=>{state.expanded=!state.expanded;renderPopulation();});
  $("scenario-shift").addEventListener("input",requestScenario);
  $("news-coefficient").addEventListener("input",requestScenario);
  document.querySelectorAll("[data-map-office]").forEach(button=>button.addEventListener("click",()=>loadPredictions(button.dataset.mapOffice)));
  document.querySelectorAll("[data-table-office]").forEach(button=>button.addEventListener("click",()=>loadTable(button.dataset.tableOffice)));
  document.querySelectorAll("[data-sort]").forEach(button=>button.addEventListener("click",()=>{
    const key=button.dataset.sort;
    state.table.dir=state.table.sort===key?-state.table.dir:(key==="label"||key==="rating"?1:-1);state.table.sort=key;renderTable();
  }));
  $("table-search").addEventListener("input",event=>{state.table.search=event.target.value;renderTable();});
  $("race-table-body").addEventListener("click",event=>{const row=event.target.closest?.("tr[data-race]");if(row)openTableRace(row.dataset.race);});
  ["picks-underdogs","picks-close"].forEach(id=>{
    $(id).addEventListener("click",event=>{const row=event.target.closest?.("tr[data-race]");if(row)openTableRace(row.dataset.race);});
    $(id).addEventListener("keydown",event=>{const row=event.target.closest?.("tr[data-race]");if(row&&(event.key==="Enter"||event.key===" ")){event.preventDefault();openTableRace(row.dataset.race);}});
  });
  $("race-table-body").addEventListener("keydown",event=>{const row=event.target.closest?.("tr[data-race]");if(row&&(event.key==="Enter"||event.key===" ")){event.preventDefault();openTableRace(row.dataset.race);}});
  document.addEventListener("keydown",event=>{if(event.key==="Escape")hideMapTooltip();});
  window.addEventListener("scroll",hideMapTooltip,{passive:true});
  document.querySelectorAll(".section-nav a").forEach(link=>link.addEventListener("click",()=>{
    document.querySelectorAll(".section-nav a").forEach(item=>item.classList.toggle("active",item===link));
  }));
  initialize();
})();
