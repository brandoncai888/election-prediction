"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const state = { summary: null, races: [], groups: {}, detail: null, selectedId: null, office: "", state: "", search: "", request: 0, mapOffice:"house", predictions:{}, mapRequest:0, geography:null, houseLayout:null, tab:"overview", loaded:{}, table:{office:"senate",sort:"closeness",dir:1,search:"",request:0} };
  const marginColors = {safe_d:"#1c408c",likely_d:"#577ccc",lean_d:"#8aafff",tilt_d:"#949bb3",tossup:"#9e8767",tilt_r:"#cf8980",lean_r:"#ff8b98",likely_r:"#ff5865",safe_r:"#bf1d29",unavailable:"#d9d9d9",holdover_d:"#0f2a63",holdover_r:"#8a1420"};
  const ratingOrder = ["safe_d","likely_d","lean_d","tilt_d","tossup","tilt_r","lean_r","likely_r","safe_r"];
  const ratingLabel = rating => ({safe_d:"Safe D",likely_d:"Likely D",lean_d:"Lean D",tilt_d:"Tilt D",tossup:"Toss-up",tilt_r:"Tilt R",lean_r:"Lean R",likely_r:"Likely R",safe_r:"Safe R"}[rating] || "No estimate");
  const winPct = value => finite(value) ? `${value >= .995 ? ">99" : value <= .005 ? "<1" : (value*100).toFixed(0)}%` : "—";
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
  const date = value => { if (!value) return "Date not recorded"; const parsed = new Date(String(value).length === 10 ? `${value}T12:00:00` : value); return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleDateString("en-US", {year:"numeric",month:"short",day:"numeric"}); };
  const show = (id, visible) => { $(id).hidden = !visible; };
  const announce = message => { $("live-status").textContent = message; };

  // A static export (python main.py export-site) replaces /api/ with JSON files.
  const STATIC = Boolean(document.querySelector('meta[name="election-static"]'));

  function dataPath(path) {
    if (!STATIC) return path;
    const url = new URL(path, "http://local");
    const query = name => url.searchParams.get(name) || "";
    if (url.pathname === "/api/race") return `data/race/${encodeURIComponent(query("id"))}.json`;
    if (url.pathname === "/api/predictions") return `data/predictions-${encodeURIComponent(query("office"))}.json`;
    if (url.pathname.startsWith("/api/")) return `data/${url.pathname.slice(5)}.json`;
    return path.replace(/^\//, "");
  }

  // Undo the compaction in electiondb/site.py. The district page no longer lists news, so shared news is not fetched.
  function expandDetail(detail) {
    if (!STATIC) return detail;
    const groups = detail.projection?.groups || [];
    if (!detail.forecasts) detail.forecasts = groups;
    if (detail.demographics_from_groups) detail.demographics = groups.map(group => ({...group, ...detail.demographics_from_groups}));
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
    $("as-of").textContent = summary.as_of ? `Data as of ${date(summary.as_of)}` : "2026 election cycle";
    const limits = Array.isArray(summary.limitations) ? summary.limitations : [];
    if (limits.length) $("limitations-list").innerHTML = limits.map(limit => `<li>${escape(limit)}</li>`).join("");
  }

  // Tabs: the hash keeps the open tab (and the selected race) across reloads and links.
  const TABS = ["overview","map","table","district"];
  function showTab(name, {focus=false}={}) {
    if (!TABS.includes(name)) name = "overview";
    state.tab = name;
    document.querySelectorAll("[data-tab]").forEach(button => {
      const active = button.dataset.tab === name;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
      if (active && focus) button.focus();
    });
    TABS.forEach(tab => show(`panel-${tab}`, tab === name));
    hideMapTooltip();
    if (name === "map" && !state.loaded.map) { state.loaded.map = true; loadPredictions(state.mapOffice); }
    if (name === "table" && !state.loaded.table) { state.loaded.table = true; loadTable(state.table.office); }
  }
  function setHash(hash) { if (window.location.hash !== hash) history.replaceState(null, "", hash); }
  function routeFromHash() {
    const [tab, raceId] = window.location.hash.replace(/^#/, "").split("/");
    if (!TABS.includes(tab)) return false;
    showTab(tab);
    if (tab === "district" && raceId) {
      const race = state.races.find(item => String(item.race_id) === decodeURIComponent(raceId));
      if (race && String(race.race_id) !== String(state.selectedId)) selectRace(race);
    }
    return true;
  }
  function openRace(id) {
    const race = state.races.find(item => String(item.race_id) === String(id));
    if (!race) return;
    showTab("district");
    selectRace(race);
    window.scrollTo({top:0, behavior:"smooth"});
  }

  function populateStates() {
    const states = [...new Set(state.races.map(raceState).filter(Boolean))].sort((a,b) => (stateNames[a] || a).localeCompare(stateNames[b] || b));
    $("state-filter").innerHTML = '<option value="">All states</option>' + states.map(code => `<option value="${escape(code)}">${escape(stateNames[code] || code)}</option>`).join("");
    $("state-filter").value = state.state;
  }

  function renderRaceList() {
    const query = state.search.toLowerCase().trim();
    const visible = state.races.filter(race => (!state.office || raceOffice(race) === state.office) && (!state.state || raceState(race) === state.state) && (!query || `${race.label} ${race.state} ${stateNames[raceState(race)] || ""} ${race.district ?? ""} ${race.office}`.toLowerCase().includes(query)));
    $("race-count").textContent = `${fmt(visible.length)} ${visible.length === 1 ? "race" : "races"}`;
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
    state.selectedId = id;
    if (state.tab === "district") setHash(`#district/${encodeURIComponent(id)}`);
    const request = ++state.request;
    renderRaceList();
    updateMapSelection();
    $("race-detail").setAttribute("aria-busy", "true");
    $("detail-loading").textContent = `Loading ${race.label || "race"}…`;
    show("detail-loading", true); show("detail-content", false);
    try {
      const detail = expandDetail(await api(`/api/race?id=${encodeURIComponent(id)}`));
      if (request !== state.request) return;
      state.detail = detail;
      renderDetail();
      show("detail-content", true); show("detail-loading", false);
      announce(`${detail.race?.label || race.label} loaded.`);
    } catch (error) {
      if (request !== state.request) return;
      $("detail-loading").textContent = `Could not load this race. ${error.message}`;
    } finally {
      if (request === state.request) $("race-detail").setAttribute("aria-busy", "false");
    }
  }

  function renderDetail() {
    const race = state.detail.race || {};
    const projection = state.detail.projection || {};
    $("race-kicker").textContent = `2026 ${String(race.election_type || "general").replace(/_/g," ").toUpperCase()} ELECTION`;
    $("race-title").textContent = race.label || "Selected race";
    $("race-subtitle").textContent = `${stateNames[raceState(race)] || race.state || "Constituency"} · ${race.election_date ? date(race.election_date) : "Election date not recorded"}`;
    $("race-office").textContent = officeLabel(race.office);
    $("boundary-note").textContent = race.boundary_note || "";
    show("boundary-note", Boolean(race.boundary_note));
    renderRaceForecast(projection);
    renderShiftSummary(projection);
    renderForecastCohorts();
  }

  function groupOptions(field) {
    const key = {race_group:"race_groups",sex:"sexes",education:"education"}[field];
    const source = Array.isArray(state.groups[key]) ? state.groups[key] : [];
    const options = new Map(source.map(item => typeof item === "string" ? [item, titleCase(item)] : [String(item.value), item.label || titleCase(item.value)]));
    [...rows("demographics"), ...rows("forecasts")].forEach(row => { if (row[field] != null && !options.has(String(row[field]))) options.set(String(row[field]), titleCase(row[field])); });
    return [...options].map(([value,label]) => ({value,label}));
  }

  function groupLabel(field, value) {
    return groupOptions(field).find(item => item.value === String(value))?.label || titleCase(value);
  }

  function renderRaceForecast(projection) {
    const aggregate=projection.race_prediction || projection;
    const share=aggregate.dem_share ?? aggregate.projected_dem_share;
    show("race-forecast-card",finite(share));
    if(!finite(share))return;
    const margin=(share-.5)*200;
    const winner=margin>=0 ? "Democratic" : "Republican";
    const color=margin>=0?"#1f4e87":"#9d3346";
    $("race-forecast-card").innerHTML=`<div class="race-forecast-top"><div><p class="eyebrow">2026 TURNOUT-WEIGHTED FORECAST</p><strong style="color:${color}">${winner} +${Math.abs(margin).toFixed(1)}</strong><span>Projected two-party margin · experimental model</span>${finite(aggregate.win_probability_d)?`<span class="win-odds">Win probability: <b style="color:#1c408c">D ${winPct(aggregate.win_probability_d)}</b> · <b style="color:#bf1d29">R ${winPct(1-aggregate.win_probability_d)}</b> · ${escape(ratingLabel(aggregate.rating))}</span>`:""}</div><button type="button" class="text-link" data-scroll="forecast-cohorts">Inspect the demographic model ↓</button></div><div class="race-share-labels"><span>Democratic <b>${pct(share)}</b></span><span>Republican <b>${pct(1-share)}</b></span></div><div class="race-share-bar"><span style="width:${share*100}%"></span></div><div class="race-forecast-facts"><span>2024 baseline D share <b>${pct(aggregate.baseline_dem_share)}</b></span><span>National environment <b>${pp(aggregate.environment_shift_pp)}</b></span><span>Midterm turnout <b>${pp(aggregate.turnout_shift_pp)}</b></span><span>Group news <b>${pp(aggregate.news_margin_shift_pp)}</b></span></div><p class="race-forecast-note">Shifts are in margin points. Cohorts are weighted by population × validated turnout for their race, sex and education. Residents age 25+ are the population base; noncitizens are not removed.</p>`;
  }

  function renderShiftSummary(projection) {
    const feature=projection.news_feature || {};
    const drivers=Array.isArray(projection.drivers)?projection.drivers:[];
    const env=projection.national_environment || {};
    const margin=share=>finite(share)?`${share>=.5?"D":"R"} +${Math.abs(200*share-100).toFixed(1)}`:"—";
    $("news-signal-summary").innerHTML=`<h4>What moves this forecast from 2024</h4><div class="news-signal-stats"><span>Baseline (House vote)<strong>${margin(env.target_two_party_d)}</strong></span><span>Final, with turnout &amp; news<strong>${margin(env.final_two_party_d)}</strong></span><span>Typical midterm gain<strong>${margin(env.prior_two_party_d)}</strong></span><span>Generic-ballot polls<strong>${margin(env.poll_two_party_d)}</strong></span></div><p>The national baseline averages the typical out-party midterm gain since 2010 with the generic-ballot polling average. Every group moves toward it in proportion to how strongly that group swung in the 2018 midterm (CES survey). Each group is weighted by its validated midterm turnout, and news coverage of a specific racial group then moves that group on top of the baseline, by a coefficient fitted to the 2010–2022 midterms.</p>${drivers.length?`<div class="news-driver-list">${drivers.map(driver=>`<span>${escape(titleCase(driver.issue))} (margin)<b>${pp(2*driver.contribution_pp)}</b></span>`).join("")}</div>`:""}${renderGroupSignals(feature, state.summary?.model?.news_response)}`;
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

  function renderForecastCohorts() {
    const cohorts=rows("forecasts").filter(row=>finite(row.projected_dem_share));
    show("forecast-cohorts",cohorts.length>0);
    if(!cohorts.length)return;
    const total=cohorts.reduce((sum,row)=>sum+(row.population || 0),0);
    const ranked=cohorts.slice().sort((a,b)=>(b.weight ?? b.population ?? 0)-(a.weight ?? a.population ?? 0));
    $("forecast-cohort-table").innerHTML=ranked.map(row=>{
      const swing=row.swing_pp ?? (row.projected_dem_share-row.baseline_dem_share)*100;
      const weight=row.weight ?? row.population_weight ?? (total>0?(row.population || 0)/total:null);
      return `<tr><td><span class="cohort-name">${escape(groupLabel("race_group",row.race_group))} · ${escape(groupLabel("sex",row.sex))}</span><small>${escape(groupLabel("education",row.education))}</small></td><td class="numeric">${pct(weight)}<small>${fmt(row.population)} residents</small></td><td class="numeric">${pct(row.baseline_dem_share)}</td><td class="numeric"><strong>${pct(row.projected_dem_share)}</strong></td><td class="numeric ${swing>=0?"swing-dem":"swing-rep"}">${pp(swing)}</td></tr>`;
    }).join("");
    $("forecast-weight-note").textContent=`${fmt(cohorts.length)} demographic combinations · ${fmt(total)} modeled residents age 25+, sorted by share of expected voters. Education is grouped into high school or less and some college or more. The 2024 shares are fitted survey baselines, not observed district results.`;
  }

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
      if(!state.geography)state.geography=await api("/maps/us-states.json");
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
    $("map-geography-note").textContent=senate?"State outlines: US Census Bureau, 2024. Alaska and Hawaii are insets at different scales. Gray states have no 2026 Senate election; each race is listed below.":"One dot per House district, placed inside its state and numbered roughly north to south. Dots do not mark district locations. Small northeastern delegations spill slightly past their borders. Alaska and Hawaii are insets.";
    if(senate)renderSenateMap(races);else renderHouseMap(races);
    show("map-loading",false);show("house-map",!senate);show("senate-map",senate);
    renderCompetitiveRaces(races);updateMapSelection();
  }

  function albers([longitude,latitude]) {
    const radians=Math.PI/180, phi=latitude*radians, n=(Math.sin(29.5*radians)+Math.sin(45.5*radians))/2;
    const c=Math.cos(29.5*radians)**2+2*n*Math.sin(29.5*radians), rho=Math.sqrt(c-2*n*Math.sin(phi))/n;
    const rho0=Math.sqrt(c-2*n*Math.sin(37.5*radians))/n, theta=n*(longitude+96)*radians;
    return [rho*Math.sin(theta),rho0-rho*Math.cos(theta)];
  }

  function geometryRings(feature){return feature.geometry.type==="MultiPolygon"?feature.geometry.coordinates.flat():feature.geometry.coordinates;}

  // Albers for the lower 48 fitted to a 1000×640 view, with Alaska and Hawaii as insets.
  function mapProjection(features) {
    const continental=features.filter(feature=>!["AK","HI"].includes(feature.properties.state)).flatMap(feature=>geometryRings(feature).flatMap(ring=>ring.map(albers)));
    const xs=continental.map(point=>point[0]),ys=continental.map(point=>point[1]),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys);
    const scale=Math.min(900/(xmax-xmin),540/(ymax-ymin));
    return (point,code)=>{
      if(code==="AK"){let longitude=point[0];if(longitude>0)longitude-=360;return [35+(longitude+188)/60*200,535-(point[1]-51)/21*125];}
      if(code==="HI")return [275+(point[0]+160.6)/7*120,550-(point[1]-18.7)/4*75];
      const [x,y]=albers(point);return [50+(x-xmin)*scale,35+(ymax-y)*scale];
    };
  }
  const statePath=(feature,project)=>geometryRings(feature).map(ring=>ring.map((point,index)=>`${index?"L":"M"}${project(point,feature.properties.state).map(value=>value.toFixed(2)).join(",")}`).join("")+"Z").join("");

  function insideRings([x,y],rings) {
    let inside=false;
    for(const ring of rings)for(let i=0,j=ring.length-1;i<ring.length;j=i++){
      const [xi,yi]=ring[i],[xj,yj]=ring[j];
      if((yi>y)!==(yj>y)&&x<(xj-xi)*(y-yi)/(yj-yi)+xi)inside=!inside;
    }
    return inside;
  }

  // Dot positions for every House seat: a hex lattice inside each state's outline,
  // spaced so the state's seats spread over its area, then nudged apart where small
  // neighboring delegations (NJ, CT, RI, MA, MD, DE) collide.
  const DOT=5.2, GAP=12;
  function houseLayout(features,project,seatCounts) {
    const dots=[];
    features.forEach(feature=>{
      const code=feature.properties.state, count=seatCounts.get(code) || 0;
      if(!count)return;
      const rings=geometryRings(feature).map(ring=>ring.map(point=>project(point,code)));
      const all=rings.flat(), xs=all.map(p=>p[0]), ys=all.map(p=>p[1]);
      const box=[Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)];
      const fine=[];
      for(let y=box[1];y<=box[3];y+=3)for(let x=box[0];x<=box[2];x+=3)if(insideRings([x,y],rings))fine.push([x,y]);
      const center=fine.length?[fine.reduce((s,p)=>s+p[0],0)/fine.length,fine.reduce((s,p)=>s+p[1],0)/fine.length]:[(box[0]+box[2])/2,(box[1]+box[3])/2];
      const area=Math.max(fine.length*9,1);
      const lattice=spacing=>{
        const points=[],rowHeight=spacing*Math.sqrt(3)/2,pad=spacing*Math.ceil(Math.sqrt(count)+2);
        for(let row=Math.floor((box[1]-pad-center[1])/rowHeight);center[1]+row*rowHeight<=box[3]+pad;row++){
          const y=center[1]+row*rowHeight, offset=(row&1)*spacing/2;
          for(let col=Math.floor((box[0]-pad-center[0])/spacing)-1;center[0]+col*spacing+offset<=box[2]+pad;col++){
            const x=center[0]+col*spacing+offset;
            points.push({x,y,inside:insideRings([x,y],rings),distance:Math.hypot(x-center[0],y-center[1])});
          }
        }
        return points;
      };
      let spacing=Math.min(26,Math.max(GAP,Math.sqrt(area/(count*0.866))));
      let points=lattice(spacing);
      while(points.filter(p=>p.inside).length<count&&spacing>GAP){spacing=Math.max(GAP,spacing*0.9);points=lattice(spacing);}
      const chosen=points.sort((a,b)=>(b.inside-a.inside)||(a.distance-b.distance)).slice(0,count);
      chosen.sort((a,b)=>Math.round(a.y/(spacing*0.8))-Math.round(b.y/(spacing*0.8))||a.x-b.x);
      chosen.forEach((point,index)=>dots.push({code,index,x:point.x,y:point.y,tx:point.x,ty:point.y}));
    });
    for(let pass=0;pass<60;pass++){
      for(let i=0;i<dots.length;i++)for(let j=i+1;j<dots.length;j++){
        const a=dots[i],b=dots[j],dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||0.01;
        if(d<GAP){const push=(GAP-d)/2,ux=dx/d,uy=dy/d;a.x-=ux*push;a.y-=uy*push;b.x+=ux*push;b.y+=uy*push;}
      }
      dots.forEach(dot=>{dot.x+=(dot.tx-dot.x)*0.04;dot.y+=(dot.ty-dot.y)*0.04;});
    }
    return dots;
  }

  function renderHouseMap(races) {
    const namespace="http://www.w3.org/2000/svg",svg=document.createElementNS(namespace,"svg");
    svg.setAttribute("viewBox","0 0 1000 640");svg.setAttribute("role","group");svg.setAttribute("aria-label","2026 House race projections, one dot per district");
    const features=state.geography?.features || [];
    const project=mapProjection(features);
    const byState=new Map();
    races.forEach(race=>{const code=raceState(race);if(!byState.has(code))byState.set(code,[]);byState.get(code).push(race);});
    byState.forEach(seats=>seats.sort((a,b)=>Number(a.district ?? String(a.race_id).split("-").at(-1))-Number(b.district ?? String(b.race_id).split("-").at(-1))));
    state.houseLayout ||= houseLayout(features,project,new Map([...byState].map(([code,seats])=>[code,seats.length])));
    features.forEach(feature=>{
      const path=document.createElementNS(namespace,"path");
      path.setAttribute("d",statePath(feature,project));path.setAttribute("class","house-state-outline");path.setAttribute("fill-rule","evenodd");
      svg.append(path);
    });
    state.houseLayout.forEach(dot=>{
      const race=byState.get(dot.code)?.[dot.index];
      if(!race)return;
      const circle=document.createElementNS(namespace,"circle");
      circle.setAttribute("cx",dot.x.toFixed(1));circle.setAttribute("cy",dot.y.toFixed(1));circle.setAttribute("r",DOT);
      circle.setAttribute("fill",predictionColor(race));circle.setAttribute("class","house-dot");
      circle.setAttribute("role","button");circle.setAttribute("tabindex","0");circle.dataset.mapRace=race.race_id;
      circle.setAttribute("aria-label",`${race.label}: ${predictionDescription(race)}. Open race.`);
      attachMapInteraction(circle,[race]);svg.append(circle);
    });
    const insetLabel=document.createElementNS(namespace,"text");insetLabel.setAttribute("x","35");insetLabel.setAttribute("y","580");insetLabel.setAttribute("class","inset-caption");insetLabel.textContent="AK / HI · not to scale";svg.append(insetLabel);
    $("house-map").replaceChildren(svg);
  }

  function renderSenateMap(races) {
    const namespace="http://www.w3.org/2000/svg",svg=document.createElementNS(namespace,"svg");
    svg.setAttribute("viewBox","0 0 1000 640");svg.setAttribute("role","group");svg.setAttribute("aria-label","2026 Senate race projections by state");
    const features=state.geography?.features || [];
    const project=mapProjection(features);
    const byState=new Map();races.forEach(race=>{const code=raceState(race);if(!byState.has(code))byState.set(code,[]);byState.get(code).push(race);});
    const callouts={MA:190,RI:220,CT:250,NJ:295,DE:325,MD:355};
    features.forEach(feature=>{
      const code=feature.properties.state,contests=byState.get(code) || [];
      const closest=contests.slice().sort((a,b)=>Math.abs(predictionMargin(a) || 0)-Math.abs(predictionMargin(b) || 0))[0];
      const path=document.createElementNS(namespace,"path");
      path.setAttribute("d",statePath(feature,project));
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
    element.addEventListener("click",()=>openRace(races[0].race_id));
    if(element.tagName.toLowerCase()!=="button")element.addEventListener("keydown",event=>{if(event.key==="Enter" || event.key===" "){event.preventDefault();openRace(races[0].race_id);}});
  }
  function showMapTooltip(event,races) {
    $("map-tooltip").innerHTML=races.map(race=>`<strong>${escape(race.label)}</strong><span>${escape(ratingLabel(race.rating))} · ${escape(predictionDescription(race))}</span><small>D win ${winPct(race.win_probability_d)} · R win ${winPct(race.win_probability_r)}<br>2024 baseline ${finite(race.baseline_margin_pp)?`${race.baseline_margin_pp>=0?"D":"R"} +${Math.abs(race.baseline_margin_pp).toFixed(1)}`:"—"} · news shift ${pp(race.news_margin_shift_pp)}${race.district_redrawn_for_2026?"<br>District redrawn for 2026":""}<br>Click to see what drives this race</small>`).join('<hr>');
    show("map-tooltip",true);positionMapTooltip(event.clientX,event.clientY);
  }
  function positionMapTooltip(x,y) {
    const tooltip=$("map-tooltip"),bounds=tooltip.getBoundingClientRect();
    tooltip.style.left=`${Math.max(12,Math.min(x+14,window.innerWidth-bounds.width-12))}px`;
    tooltip.style.top=`${Math.max(12,y+bounds.height+26>window.innerHeight?y-bounds.height-12:y+18)}px`;
  }
  function hideMapTooltip(){show("map-tooltip",false);}
  function updateMapSelection(){document.querySelectorAll("[data-map-race]").forEach(element=>element.classList.toggle("selected-seat",String(element.dataset.mapRace)===String(state.selectedId)));}
  function renderCompetitiveRaces(races) {
    const closest=races.filter(race=>finite(predictionMargin(race))).sort((a,b)=>Math.abs(predictionMargin(a))-Math.abs(predictionMargin(b))).slice(0,12);
    $("competitive-count").textContent=`${closest.length} races`;
    $("competitive-list").replaceChildren();
    closest.forEach(race=>{
      const button=document.createElement("button");button.type="button";button.dataset.mapRace=race.race_id;button.innerHTML=`<i style="background:${predictionColor(race)}"></i><span>${escape(race.label)}</span><strong>${finite(predictionMargin(race))?`${predictionMargin(race)>=0?"D":"R"} +${Math.abs(predictionMargin(race)).toFixed(1)}`:"—"}</strong>`;button.addEventListener("click",()=>openRace(race.race_id));$("competitive-list").append(button);
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
    const results=await Promise.allSettled([api("/api/summary"),api("/api/races"),api("/api/groups")]);
    if(results[0].status==="fulfilled")state.summary=results[0].value;
    if(results[1].status==="fulfilled")state.races=Array.isArray(results[1].value.races)?results[1].value.races:[];
    if(results[2].status==="fulfilled")state.groups=results[2].value;
    renderSummary();populateStates();renderRaceList();
    if(results.some(result=>result.status==="rejected"))notice("Some forecast data could not be loaded. Start the local data service, then retry.");
    if(!routeFromHash())showTab("overview");
    if(state.races.length&&!state.selectedId){
      const selected=state.races.find(race=>raceState(race)==="NC"&&raceOffice(race)==="senate") || state.races.find(race=>raceOffice(race)==="senate") || state.races[0];
      await selectRace(selected);
    }else if(!state.races.length){
      $("detail-loading").textContent=results[1].status==="rejected" ? "The race directory could not be loaded. Start the local data service and use Retry above." : "No races are available yet.";
      $("race-detail").setAttribute("aria-busy","false");
    }
  }

  document.querySelectorAll("[data-tab]").forEach(button=>{
    button.addEventListener("click",()=>{showTab(button.dataset.tab);setHash(button.dataset.tab==="district"&&state.selectedId?`#district/${encodeURIComponent(state.selectedId)}`:`#${button.dataset.tab}`);});
    button.addEventListener("keydown",event=>{
      const step={ArrowRight:1,ArrowLeft:-1}[event.key];
      if(!step)return;
      event.preventDefault();
      const next=TABS[(TABS.indexOf(state.tab)+step+TABS.length)%TABS.length];
      showTab(next,{focus:true});setHash(`#${next}`);
    });
  });
  window.addEventListener("hashchange",routeFromHash);
  $("race-detail").addEventListener("click",event=>{const target=event.target.closest?.("[data-scroll]");if(target)$(target.dataset.scroll)?.scrollIntoView({behavior:"smooth"});});
  $("race-search").addEventListener("input",event=>{state.search=event.target.value;renderRaceList();});
  $("state-filter").addEventListener("change",event=>{state.state=event.target.value;renderRaceList();});
  document.querySelectorAll("[data-office]").forEach(button=>button.addEventListener("click",()=>{
    state.office=button.dataset.office;
    document.querySelectorAll("[data-office]").forEach(item=>item.setAttribute("aria-pressed",String(item===button)));
    renderRaceList();
  }));
  document.querySelectorAll("[data-map-office]").forEach(button=>button.addEventListener("click",()=>loadPredictions(button.dataset.mapOffice)));
  document.querySelectorAll("[data-table-office]").forEach(button=>button.addEventListener("click",()=>loadTable(button.dataset.tableOffice)));
  document.querySelectorAll("[data-sort]").forEach(button=>button.addEventListener("click",()=>{
    const key=button.dataset.sort;
    state.table.dir=state.table.sort===key?-state.table.dir:(key==="label"||key==="rating"?1:-1);state.table.sort=key;renderTable();
  }));
  $("table-search").addEventListener("input",event=>{state.table.search=event.target.value;renderTable();});
  ["race-table-body","picks-underdogs","picks-close"].forEach(id=>{
    $(id).addEventListener("click",event=>{const row=event.target.closest?.("tr[data-race]");if(row)openRace(row.dataset.race);});
    $(id).addEventListener("keydown",event=>{const row=event.target.closest?.("tr[data-race]");if(row&&(event.key==="Enter"||event.key===" ")){event.preventDefault();openRace(row.dataset.race);}});
  });
  document.addEventListener("keydown",event=>{if(event.key==="Escape")hideMapTooltip();});
  window.addEventListener("scroll",hideMapTooltip,{passive:true});
  initialize();
})();
