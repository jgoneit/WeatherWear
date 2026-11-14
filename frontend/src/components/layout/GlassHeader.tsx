import type { ReactNode } from "react";
import { Link } from "react-router";
import { STRINGS } from "../../lib/strings";
import { Icon } from "../Icon";
export function GlassHeader({ trailing }: { trailing?: ReactNode }) {
  return <header className="app-header">
    <Link to="/" className="brand"><span className="brand-mark"><Icon name="hanger" size={25}/></span><span>{STRINGS.appName}<small>날씨를 입는 나만의 기록</small></span></Link>
    {trailing ? <div>{trailing}</div> : null}
  </header>;
}
