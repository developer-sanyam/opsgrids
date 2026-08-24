import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { getAudiences, getCampaigns } from "@/api/client";
import type { Audience, Campaign } from "@/api/types";
import { campaignsForAudience } from "@/data/derive";
import { useAsync } from "@/lib/useAsync";
import { formatMoneyCompact, formatNumber } from "@/lib/format";
import {
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  SearchInput,
  Skeleton,
} from "@/ui";
import { ArrowRightIcon, UsersIcon } from "@/ui/icons";

const CURRENCY = "INR";
const NO_AUDIENCES: Audience[] = [];
const NO_CAMPAIGNS: Campaign[] = [];

interface AudienceView {
  audience: Audience;
  campaigns: Campaign[];
  influencedRevenue: number;
}

export function AudiencesPage() {
  const [search, setSearch] = useState("");
  const loader = useCallback(() => Promise.all([getAudiences(), getCampaigns()]), []);
  const data = useAsync(loader, []);

  const audiences = data.status === "success" ? data.data[0].items : NO_AUDIENCES;
  const campaigns = data.status === "success" ? data.data[1].items : NO_CAMPAIGNS;

  const views: AudienceView[] = useMemo(() => {
    const q = search.trim().toLowerCase();
    return audiences
      .filter(
        (a) =>
          q === "" ||
          a.name.toLowerCase().includes(q) ||
          a.criteria.toLowerCase().includes(q),
      )
      .map((audience) => {
        const used = campaignsForAudience(campaigns, audience.id);
        return {
          audience,
          campaigns: used,
          influencedRevenue: used.reduce((sum, c) => sum + c.revenue_minor, 0),
        };
      })
      .sort((a, b) => b.campaigns.length - a.campaigns.length);
  }, [audiences, campaigns, search]);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Reach"
        title="Audiences"
        actions={
          <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
            <UsersIcon className="size-4 text-muted" />
            <span className="tnum">{audiences.length}</span>
            <span className="text-muted font-medium">segments</span>
          </span>
        }
      />

      <Card className="p-4 sm:p-5">
        <SearchInput
          label="Search audiences"
          placeholder="Search by name or criteria…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          containerClassName="sm:max-w-sm"
        />
      </Card>

      {data.status === "loading" ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Card key={i} className="p-6 flex flex-col gap-4">
              <div className="flex gap-3">
                <Skeleton className="size-11 rounded-tile" />
                <div className="flex-1 flex flex-col gap-2">
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-3 w-20" />
                </div>
              </div>
              <Skeleton className="h-10 w-full rounded-tile" />
              <Skeleton className="h-9 w-40 rounded-full" />
            </Card>
          ))}
        </div>
      ) : null}

      {data.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={data.error.message} />
        </Card>
      ) : null}

      {data.status === "success" ? (
        views.length === 0 ? (
          <Card className="p-6">
            <EmptyState
              icon={<UsersIcon className="size-6" />}
              title="No matching audiences"
              description="No segment matches that search. Try a different name or criteria keyword."
            />
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {views.map(({ audience, campaigns: used, influencedRevenue }) => (
              <Card key={audience.id} className="flex flex-col gap-4 p-6">
                <div className="flex items-start gap-3">
                  <span className="grid size-11 shrink-0 place-items-center rounded-tile bg-accent-soft text-accent-ink">
                    <UsersIcon className="size-5" />
                  </span>
                  <div className="flex min-w-0 flex-col leading-tight">
                    <span className="font-bold text-ink">{audience.name}</span>
                    <span className="text-sm text-muted tnum">
                      {formatNumber(audience.size)} reachable people
                    </span>
                  </div>
                </div>

                <p className="min-h-10 text-sm text-ink-soft">{audience.criteria}</p>

                <div className="grid grid-cols-2 gap-2 border-t border-line pt-4">
                  <div className="flex flex-col">
                    <span className="display text-lg font-extrabold tnum">{used.length}</span>
                    <span className="text-xs text-muted">
                      {used.length === 1 ? "campaign" : "campaigns"}
                    </span>
                  </div>
                  <div className="flex flex-col">
                    <span className="display text-lg font-extrabold tnum">
                      {formatMoneyCompact(influencedRevenue, CURRENCY)}
                    </span>
                    <span className="text-xs text-muted">revenue influenced</span>
                  </div>
                </div>

                {used.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5">
                    {used.slice(0, 3).map((c) => (
                      <Link
                        key={c.id}
                        to={`/campaigns/${c.id}`}
                        className="inline-flex max-w-full items-center gap-1 rounded-full bg-ground px-2.5 py-1 text-xs font-semibold text-ink-soft hover:bg-accent-soft hover:text-accent-ink transition-colors"
                      >
                        <span className="truncate">{c.name}</span>
                        <ArrowRightIcon className="size-3 shrink-0" />
                      </Link>
                    ))}
                    {used.length > 3 ? (
                      <span className="inline-flex items-center rounded-full bg-ground px-2.5 py-1 text-xs font-semibold text-faint">
                        +{used.length - 3} more
                      </span>
                    ) : null}
                  </div>
                ) : (
                  <span className="text-xs text-faint">Not targeted by any campaign yet.</span>
                )}
              </Card>
            ))}
          </div>
        )
      ) : null}
    </div>
  );
}
