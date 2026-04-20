package sessions

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
)

func RegisterHandlers(mux *http.ServeMux, manager *Manager) {
	mux.HandleFunc("/api/health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})

	mux.HandleFunc("/api/sessions", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			writeJSON(w, http.StatusOK, manager.List())
		case http.MethodPost:
			var req StartRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				writeError(w, http.StatusBadRequest, "invalid JSON body")
				return
			}
			summary, err := manager.Start(req)
			if err != nil {
				writeError(w, http.StatusBadRequest, err.Error())
				return
			}
			writeJSON(w, http.StatusCreated, summary)
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	})

	mux.HandleFunc("/api/sessions/", func(w http.ResponseWriter, r *http.Request) {
		id, tail := splitSessionPath(r.URL.Path)
		if id == "" {
			writeError(w, http.StatusNotFound, "session not found")
			return
		}

		session, ok := manager.Get(id)
		if !ok {
			writeError(w, http.StatusNotFound, "session not found")
			return
		}

		if tail == "" {
			switch r.Method {
			case http.MethodGet:
				writeJSON(w, http.StatusOK, session.Summary())
			case http.MethodDelete:
				if r.URL.Query().Get("drop_db") == "true" {
					manager.StopWithDropDB(id)
				} else {
					manager.Stop(id)
				}
				writeJSON(w, http.StatusOK, map[string]string{"status": "stopping"})
			default:
				w.WriteHeader(http.StatusMethodNotAllowed)
			}
			return
		}

		if tail == "events" && r.Method == http.MethodGet {
			streamEvents(w, r, session)
			return
		}

		writeError(w, http.StatusNotFound, "route not found")
	})
}

func splitSessionPath(path string) (string, string) {
	rest := strings.TrimPrefix(path, "/api/sessions/")
	parts := strings.SplitN(rest, "/", 2)
	id := strings.TrimSpace(parts[0])
	if len(parts) == 1 {
		return id, ""
	}
	return id, strings.Trim(parts[1], "/")
}

func streamEvents(w http.ResponseWriter, r *http.Request, session *Session) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "streaming is not supported")
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")

	for _, event := range session.Events() {
		writeSSE(w, event)
	}
	flusher.Flush()

	ch, unsubscribe := session.Subscribe()
	defer unsubscribe()

	notify := r.Context().Done()
	for {
		select {
		case <-notify:
			return
		case event, ok := <-ch:
			if !ok {
				return
			}
			writeSSE(w, event)
			flusher.Flush()
		}
	}
}

func writeSSE(w http.ResponseWriter, event Event) {
	payload, _ := json.Marshal(event)
	fmt.Fprintf(w, "event: crawler\n")
	fmt.Fprintf(w, "data: %s\n\n", payload)
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]string{"error": message})
}