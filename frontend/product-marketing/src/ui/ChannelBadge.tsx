import type { Channel } from "@/api/types";
import { channelLabel } from "@/lib/format";
import { cn } from "@/lib/cn";
import { CHANNEL_ICON, CHANNEL_TONE } from "./channelMeta";

/** An icon + label chip identifying a campaign's channel. */
export function ChannelBadge({
  channel,
  className,
}: {
  channel: Channel;
  className?: string;
}) {
  const Icon = CHANNEL_ICON[channel];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full py-1 pl-1.5 pr-2.5 text-xs font-semibold",
        CHANNEL_TONE[channel],
        className,
      )}
    >
      <span className="grid size-5 place-items-center rounded-full bg-surface/70">
        <Icon className="size-3.5" />
      </span>
      {channelLabel(channel)}
    </span>
  );
}
