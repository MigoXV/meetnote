import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, terminal } from "../api";
import type { Info, RunEvent, Sample, SessionRow, Snapshot } from "../api";

export function useWorkspace(reportError: (message: string) => void) {
  const [info, setInfo] = useState<Info | null>(null);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [selected, setSelected] = useState("");
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connection, setConnection] = useState("等待连接");
  const selectedRef = useRef("");

  const refreshSessions = useCallback(async () => {
    setSessions(await api<SessionRow[]>("/sessions"));
  }, []);

  const refresh = useCallback(async (id: string) => {
    const result = await api<Snapshot>("/sessions/" + id);
    if (selectedRef.current === id) setSnapshot(result);
    setSessions((previous) =>
      previous.map((row) =>
        row.id === id
          ? {
              id,
              title: result.title,
              status: result.status,
              accepted_count: result.accepted_count,
            }
          : row,
      ),
    );
    return result;
  }, []);

  const select = useCallback((id: string) => {
    selectedRef.current = id;
    setSelected(id);
    setSnapshot(null);
    setEvents([]);
    setConnection(id ? "正在连接" : "等待连接");
  }, []);

  const create = async (title = "临时会议", meeting_date = "") => {
    const state = await api<Snapshot>("/sessions", "POST", {
      title,
      meeting_date,
    });
    select(state.id);
    setSnapshot(state);
    await refreshSessions();
    return state.id;
  };

  useEffect(() => {
    let alive = true;
    Promise.all([
      api<Info>("/info"),
      api<Sample[]>("/samples"),
      api<SessionRow[]>("/sessions"),
    ])
      .then(([meta, corpus, rows]) => {
        if (alive) {
          setInfo(meta);
          setSamples(corpus);
          setSessions(rows);
        }
      })
      .catch((e) => {
        if (alive) reportError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [reportError]);

  useEffect(() => {
    if (!selected) return;
    let alive = true;
    const stream = new EventSource(`/api/v1/sessions/${selected}/events`);
    const end = () => {
      stream.close();
      setConnection("会话已结束");
    };
    stream.onopen = () => {
      if (alive) setConnection("实时连接");
    };
    stream.onmessage = (event) => {
      if (!alive) return;
      const item: RunEvent = JSON.parse(event.data);
      if (item.type === "session.snapshot") {
        const state = item.data as unknown as Snapshot;
        setSnapshot(state);
        if (terminal(state.status)) {
          end();
          void refreshSessions().catch(() => {});
        }
      } else {
        setEvents((previous) =>
          [...previous.filter((e) => e.id !== item.id), item].slice(-160),
        );
      }
      if (
        [
          "input.processed",
          "facts.updated",
          "minutes.completed",
          "session.terminal",
          "session.cancelled",
        ].includes(item.type)
      ) {
        void refresh(selected).catch(() => {});
      }
    };
    stream.onerror = () => {
      if (alive && stream.readyState !== EventSource.CLOSED)
        setConnection("正在重连");
    };
    const poll = async () => {
      try {
        const state = await refresh(selected);
        if (alive && terminal(state.status)) end();
      } catch (e) {
        if (!alive) return;
        reportError(e instanceof Error ? e.message : String(e));
        if (e instanceof ApiError && e.status === 404) {
          stream.close();
          window.clearInterval(timer);
          setConnection("会话已释放");
        }
      }
    };
    const timer = window.setInterval(() => {
      void poll();
    }, 2000);
    void poll();
    return () => {
      alive = false;
      stream.close();
      window.clearInterval(timer);
    };
  }, [selected, refresh, refreshSessions, reportError]);

  return {
    info,
    samples,
    sessions,
    selected,
    snapshot,
    events,
    connection,
    select,
    create,
    refresh,
    refreshSessions,
  };
}
