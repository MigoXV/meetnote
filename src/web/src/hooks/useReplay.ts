import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import type { Transcript } from "../api";

interface Replay {
  session: string;
  transcript: Transcript;
  index: number;
}
const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function useReplay(reportError: (message: string) => void) {
  const [speed, setSpeed] = useState(4);
  const [replaying, setReplaying] = useState(false);
  const [progress, setProgress] = useState({ current: 0, total: 0 });
  const currentReplay = useRef<Replay | null>(null);
  const speedRef = useRef(speed);
  const running = useRef(false);
  const mounted = useRef(true);
  const generation = useRef(0);

  useEffect(() => {
    speedRef.current = speed;
  }, [speed]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      running.current = false;
    };
  }, []);

  const pause = () => {
    running.current = false;
    generation.current++;
    setReplaying(false);
  };
  const reset = () => {
    pause();
    currentReplay.current = null;
    setProgress({ current: 0, total: 0 });
  };

  async function resume() {
    if (running.current || !currentReplay.current) return;
    const replay = currentReplay.current;
    const token = ++generation.current;
    const valid = () => mounted.current && generation.current === token;
    running.current = true;
    setReplaying(true);
    try {
      while (
        running.current &&
        valid() &&
        replay.index < replay.transcript.segments.length
      ) {
        const index = replay.index;
        const segment = replay.transcript.segments[index];
        try {
          await api(`/sessions/${replay.session}/chunks`, "POST", {
            chunk_id: `${replay.transcript.id}_${segment.id}`,
            text: segment.text,
            speaker: segment.speaker,
          });
        } catch (e) {
          if (e instanceof ApiError && e.status === 429) {
            await delay(1000);
            continue;
          }
          throw e;
        }
        // 暂停时未返回的请求可以在继续后幂等重发，不能推进新的回放任务。
        if (!valid()) return;
        if (replay.index === index) replay.index = index + 1;
        setProgress({
          current: replay.index,
          total: replay.transcript.segments.length,
        });
        if (replay.index < replay.transcript.segments.length) {
          const wait =
            speedRef.current === 0
              ? 0
              : Math.max(
                  80,
                  (segment.text.length / (12 * speedRef.current)) * 1000,
                );
          const until = Date.now() + wait;
          while (running.current && valid() && Date.now() < until)
            await delay(Math.min(150, until - Date.now()));
        }
      }
      if (
        running.current &&
        valid() &&
        replay.index === replay.transcript.segments.length
      ) {
        await api(`/sessions/${replay.session}/finalize`, "POST");
      }
    } catch (e) {
      if (valid())
        reportError(
          "回放提交或自动收尾失败：" +
            (e instanceof Error ? e.message : String(e)),
        );
    } finally {
      if (valid()) {
        running.current = false;
        setReplaying(false);
      }
    }
  }

  async function start(
    sampleId: string,
    create: (title: string, meetingDate?: string) => Promise<string>,
  ) {
    reset();
    let transcript: Transcript;
    try {
      transcript = await api<Transcript>("/samples/" + sampleId);
    } catch (e) {
      throw new Error(
        "样本读取失败：" + (e instanceof Error ? e.message : String(e)),
        { cause: e },
      );
    }
    let session: string;
    try {
      session = await create(transcript.title, transcript.meeting_date);
    } catch (e) {
      throw new Error(
        "会话创建失败：" + (e instanceof Error ? e.message : String(e)),
        { cause: e },
      );
    }
    currentReplay.current = { session, transcript, index: 0 };
    setProgress({ current: 0, total: transcript.segments.length });
    void resume();
  }

  return { speed, setSpeed, replaying, progress, pause, reset, start, resume };
}
