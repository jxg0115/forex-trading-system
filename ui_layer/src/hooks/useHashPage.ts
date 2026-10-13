import { useCallback, useEffect, useRef, useState } from "react";
import { DEFAULT_PAGE, parseHash, toHash } from "../nav";

/**
 * URL hash 整页路由。
 *
 * 为什么不用 react-router：本项目只有一个静态 dist，由后端 StaticFiles 伺服，
 * 不需要嵌套路由 / 动态参数。hash 方案零依赖、零配置，且天然支持「刷新停在原页 + 浏览器后退」。
 *
 * 用法：
 *   const { page, go } = useHashPage();
 *   go("strategy")   // 跳到策略台并写入 #strategy
 */
export function useHashPage(): {
  page: string;
  go: (id: string) => void;
  back: () => void;
  /** 仅在于 hash 真的变化（后退/前进/手改地址）时 +1，用于区分「程序化跳转」与「外部导航」 */
  navSeq: number;
} {
  const [page, setPage] = useState<string>(() => parseHash(window.location.hash));
  const [navSeq, setNavSeq] = useState(0);
  // 标记「本次变化由 go() 主动发起」，避免切台逻辑把 go() 刚设好的子标签覆盖掉
  const selfNavRef = useRef(false);

  // hash 变化（浏览器前进/后退、手改地址栏）时同步内部状态
  useEffect(() => {
    const onHash = () => {
      setPage(parseHash(window.location.hash));
      if (!selfNavRef.current) setNavSeq((n) => n + 1);
      selfNavRef.current = false;
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = useCallback((id: string) => {
    const target = id === DEFAULT_PAGE ? DEFAULT_PAGE : id;
    const h = toHash(target);
    if (window.location.hash !== h) {
      selfNavRef.current = true;
      // 写入历史记录，让浏览器后退能返回上一页
      window.location.hash = h;
    }
    setPage(target);
  }, []);

  const back = useCallback(() => {
    // 优先走浏览器历史；没有历史时回默认页
    if (window.history.length > 1) {
      window.history.back();
    } else {
      window.location.hash = toHash(DEFAULT_PAGE);
      setPage(DEFAULT_PAGE);
    }
  }, []);

  return { page, go, back, navSeq };
}
