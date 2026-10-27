"use strict";

(() => {
  const API = "/api/plants";
  const REQUEST_TIMEOUT_MS = 65000;
  const elements = Object.fromEntries([
    "plant-form", "plant-fields", "plant-name", "form-title", "save-plant",
    "cancel-edit", "selected-location", "form-status", "form-error",
    "refresh-plants", "list-status", "list-error", "plant-list", "map-status",
  ].map(id => [id, document.getElementById(id)]));
  const state = { selected: null, marker: null, map: null, editingId: null, saving: false, loading: false };

  function selectLocation(lat, lon) {
    if (state.saving) return;
    // Leaflet can pan across world copies: normalize longitude before rounding.
    lon = ((lon + 180) % 360 + 360) % 360 - 180;
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90) return;
    state.selected = { lat: Number(lat.toFixed(6)), lon: Number(lon.toFixed(6)) };
    const point = [state.selected.lat, state.selected.lon];
    if (state.marker) state.marker.setLatLng(point);
    else state.marker = L.marker(point, { alt: "Selected plant location" }).addTo(state.map);
    elements["selected-location"].textContent = `Selected: ${state.selected.lat.toFixed(6)}, ${state.selected.lon.toFixed(6)}`;
  }

  function initializeMap() {
    try {
      state.map = L.map("map").setView([57.7, 11.9], 11);
      const tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      }).addTo(state.map);
      let tileFailed = false;
      tiles.on("loading", () => { tileFailed = false; });
      tiles.on("tileerror", () => {
        tileFailed = true;
        elements["map-status"].textContent = "Some map tiles could not load. Check your connection or try again later.";
      });
      tiles.on("load", () => { if (!tileFailed) elements["map-status"].textContent = ""; });
      state.map.on("click", event => selectLocation(event.latlng.lat, event.latlng.lng));
      state.map.getContainer().addEventListener("keydown", event => {
        if (event.key === "Enter" && event.target === state.map.getContainer()) {
          event.preventDefault();
          const centre = state.map.getCenter();
          selectLocation(centre.lat, centre.lng);
        }
      });
    } catch (error) {
      console.error("Map initialization failed", error);
      elements["map-status"].textContent = "The map could not load. Reload the page to try again. Saved plants are still available below.";
    }
  }

  async function request(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(path, { ...options, signal: controller.signal, headers: {
        Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}),
      } });
      if (!response.ok) {
        let detail = "";
        try {
          const body = await response.json();
          if (typeof body.detail === "string") detail = body.detail;
          else if (Array.isArray(body.detail)) detail = body.detail.map(item => item.msg).filter(Boolean).join("; ");
        } catch { /* Nginx may return an HTML error page instead of JSON. */ }
        throw new Error(`Request failed (HTTP ${response.status}). ${detail || "Garden Service may be temporarily unavailable. Please try again."}`);
      }
      if (response.status === 204) return null;
      try { return await response.json(); }
      catch (error) {
        if (controller.signal.aborted) throw error;
        throw new Error("Garden Service returned an unreadable response. Refresh the plant list before retrying a change.");
      }
    } catch (error) {
      if (controller.signal.aborted) throw new Error("The request timed out. Refresh the plant list before retrying a change.");
      if (error instanceof TypeError) throw new Error("Cannot reach Garden Service. Check your connection and try again.");
      throw error;
    } finally { clearTimeout(timer); }
  }

  function validPlant(plant) {
    return plant && Number.isInteger(plant.id) && plant.id > 0 && typeof plant.name === "string"
      && Number.isFinite(plant.lat) && Math.abs(plant.lat) <= 90
      && Number.isFinite(plant.lon) && Math.abs(plant.lon) <= 180;
  }

  function textElement(tag, text, className) {
    const element = document.createElement(tag);
    element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  function renderPlant(plant) {
    const card = textElement("li", "", "plant-card");
    card.append(textElement("h3", plant.name));
    card.append(textElement("p", `${plant.lat.toFixed(6)}, ${plant.lon.toFixed(6)}`, "muted"));
    const weather = plant.weather;
    if (plant.weather_status === "available" && weather
        && Number.isFinite(weather.rain_expected_mm) && weather.rain_expected_mm >= 0
        && typeof weather.advice === "string" && weather.advice.trim()) {
      card.append(textElement("p", weather.advice, "advice"));
      card.append(textElement("p", `Expected precipitation: ${weather.rain_expected_mm.toFixed(1)} mm`));
      const start = new Date(weather.window_start);
      const end = new Date(weather.window_end);
      card.append(textElement("p", Number.isFinite(start.getTime()) && Number.isFinite(end.getTime())
        ? `Forecast window: ${start.toLocaleString()} – ${end.toLocaleString()} (your local time)`
        : "Forecast window unavailable.", "forecast-window"));
    } else card.append(textElement("p", "Weather unavailable. Try refreshing later.", "unavailable"));
    const actions = textElement("div", "", "actions");
    const edit = textElement("button", "Edit", "secondary");
    edit.type = "button";
    edit.setAttribute("aria-label", `Edit ${plant.name}`);
    edit.addEventListener("click", () => editPlant(plant));
    const remove = textElement("button", "Delete", "danger");
    remove.type = "button";
    remove.setAttribute("aria-label", `Delete ${plant.name}`);
    remove.addEventListener("click", () => deletePlant(plant));
    edit.disabled = remove.disabled = state.saving;
    actions.append(edit, remove);
    card.append(actions);
    return card;
  }

  async function loadPlants() {
    if (state.loading) return;
    state.loading = true;
    elements["refresh-plants"].disabled = true;
    elements["plant-list"].setAttribute("aria-busy", "true");
    elements["list-status"].textContent = "Loading plants and weather advice…";
    elements["list-error"].textContent = "";
    try {
      const plants = await request(API);
      if (!Array.isArray(plants) || !plants.every(validPlant)) {
        throw new Error("Garden Service returned an unexpected plant list. Please try refreshing.");
      }
      elements["plant-list"].replaceChildren(...plants.map(renderPlant));
      elements["list-status"].textContent = plants.length ? `${plants.length} plant${plants.length === 1 ? "" : "s"} saved.` : "No plants yet. Add your first plant using the map.";
    } catch (error) {
      console.error("Loading plants failed", error);
      elements["list-status"].textContent = "Could not refresh plants. Any plants shown may be out of date.";
      elements["list-error"].textContent = error.message;
    } finally {
      state.loading = false;
      elements["refresh-plants"].disabled = state.saving;
      elements["plant-list"].setAttribute("aria-busy", "false");
    }
  }

  function setSaving(saving) {
    state.saving = saving;
    elements["plant-fields"].disabled = saving;
    elements["refresh-plants"].disabled = saving || state.loading;
    elements["plant-list"].querySelectorAll("button").forEach(button => { button.disabled = saving; });
    elements["save-plant"].textContent = saving ? "Saving…" : state.editingId === null ? "Add plant" : "Save changes";
  }

  function resetForm() {
    state.editingId = null;
    state.selected = null;
    elements["plant-form"].reset();
    if (state.marker) state.marker.remove();
    state.marker = null;
    elements["selected-location"].textContent = "No location selected.";
    elements["form-title"].textContent = "Add a plant";
    elements["save-plant"].textContent = "Add plant";
    elements["cancel-edit"].hidden = true;
  }

  function editPlant(plant) {
    if (state.saving || !state.map) return;
    state.editingId = plant.id;
    elements["plant-name"].value = plant.name;
    selectLocation(plant.lat, plant.lon);
    state.map.setView([plant.lat, plant.lon], 13);
    elements["form-title"].textContent = "Edit plant";
    elements["save-plant"].textContent = "Save changes";
    elements["cancel-edit"].hidden = false;
    elements["form-error"].textContent = "";
    elements["form-status"].textContent = "Change the name or select a new map location, then save.";
    elements["plant-name"].focus();
  }

  async function savePlant(event) {
    event.preventDefault();
    if (state.saving) return;
    elements["form-error"].textContent = "";
    elements["form-status"].textContent = "";
    const name = elements["plant-name"].value.trim();
    if (!name || [...name].length > 100) {
      elements["form-error"].textContent = "Enter a plant name between 1 and 100 characters.";
      elements["plant-name"].focus();
      return;
    }
    if (!state.selected) {
      elements["form-error"].textContent = "Select a location on the map before saving.";
      if (state.map) state.map.getContainer().focus();
      return;
    }
    const editing = state.editingId !== null;
    setSaving(true);
    elements["form-status"].textContent = "Saving plant…";
    try {
      const plant = await request(editing ? `${API}/${state.editingId}` : API, {
        method: editing ? "PATCH" : "POST", body: JSON.stringify({ name, ...state.selected }),
      });
      if (!validPlant(plant)) throw new Error("Unexpected save response. Refresh the list before retrying; the plant may have been saved.");
      resetForm();
      elements["form-status"].textContent = editing ? "Plant updated." : "Plant added successfully.";
      // Wait for an in-flight initial/manual read before refreshing after a mutation.
      await activeLoad;
      await refreshPlants();
    } catch (error) {
      console.error("Saving plant failed", error);
      elements["form-status"].textContent = "";
      elements["form-error"].textContent = `${error.message} Your input has been kept.`;
    } finally { setSaving(false); }
  }

  async function deletePlant(plant) {
    if (state.saving) return;
    setSaving(true);
    elements["form-error"].textContent = "";
    elements["form-status"].textContent = `Deleting ${plant.name}…`;
    try {
      await request(`${API}/${plant.id}`, { method: "DELETE" });
      if (state.editingId === plant.id) resetForm();
      elements["form-status"].textContent = "Plant deleted.";
      await activeLoad;
      await refreshPlants();
    } catch (error) {
      console.error("Deleting plant failed", error);
      elements["form-status"].textContent = "";
      elements["form-error"].textContent = error.message;
    } finally { setSaving(false); }
  }

  let activeLoad = Promise.resolve();
  function refreshPlants() {
    if (!state.loading) activeLoad = loadPlants();
    return activeLoad;
  }
  elements["plant-form"].addEventListener("submit", savePlant);
  elements["refresh-plants"].addEventListener("click", refreshPlants);
  elements["cancel-edit"].addEventListener("click", () => {
    resetForm();
    elements["form-error"].textContent = "";
    elements["form-status"].textContent = "Edit cancelled.";
  });
  initializeMap();
  refreshPlants();
})();
