import { useCallback, useEffect, useRef, useState } from "react";

import { createCampaign, getCampaigns, listAudiencesForPicker, updateCampaign } from "@/api/client";
import type { Audience, Campaign, CampaignIn, CampaignPatch, CampaignStatus, Channel } from "@/api/types";
import { minorToInput, parseMoneyToMinor } from "@/lib/format";
import { today } from "@/lib/now";
import { ApiError, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import { Button, ErrorState, FormDialog, Select, Skeleton, TextField } from "@/ui";
import { AudienceFormDialog } from "./AudienceFormDialog";

const CHANNEL_OPTIONS: { value: Channel; label: string }[] = [
  { value: "search", label: "Search" },
  { value: "social", label: "Social" },
  { value: "email", label: "Email" },
  { value: "events", label: "Events" },
];

const STATUS_OPTIONS: { value: CampaignStatus; label: string }[] = [
  { value: "draft", label: "Draft" },
  { value: "live", label: "Live" },
  { value: "paused", label: "Paused" },
  { value: "ended", label: "Ended" },
];

interface CampaignFormDialogProps {
  /** `null` creates. */
  campaign: Campaign | null;
  onClose: () => void;
  onSaved: (campaign: Campaign) => void;
}

/**
 * Create or edit a campaign — `POST /marketing/campaigns` / `PATCH /marketing/campaigns/{id}`.
 * One component for both; mounted only while its dialog is open, so closing it is the reset.
 *
 * `audience_id` is required by the API and there is no create-a-campaign path around it, so a
 * zero-audience organization is led here to create one first, inline, without losing what it has
 * already typed. `currency` is fixed to whatever the organization's existing campaigns already use
 * (the rollups assume one currency per org) and is only a free choice for the very first campaign;
 * it never appears on the edit form because it cannot change once set. Editing adds a "Results"
 * group — there is no ingestion path, so hand-editing spend, revenue, pipeline, leads and
 * conversions is the only way those numbers ever move; status changes happen through the
 * dedicated Launch / Pause / Resume / End actions, not here.
 */
export function CampaignFormDialog({ campaign, onClose, onSaved }: CampaignFormDialogProps) {
  const [name, setName] = useState(campaign?.name ?? "");
  const [channel, setChannel] = useState<Channel>(campaign?.channel ?? "search");
  const [status, setStatus] = useState<CampaignStatus>(campaign?.status ?? "draft");
  const [audienceId, setAudienceId] = useState(campaign?.audience_id ?? "");
  const [currency, setCurrency] = useState(campaign?.currency ?? "INR");
  const [budget, setBudget] = useState(campaign ? minorToInput(campaign.budget_minor) : "");
  const [goal, setGoal] = useState(campaign?.goal ?? "");
  const [owner, setOwner] = useState(campaign?.owner ?? "");
  const [startDate, setStartDate] = useState(campaign?.start_date ?? today());
  const [endDate, setEndDate] = useState(campaign?.end_date ?? "");

  const [spent, setSpent] = useState(campaign ? minorToInput(campaign.spent_minor) : "0");
  const [revenue, setRevenue] = useState(campaign ? minorToInput(campaign.revenue_minor) : "0");
  const [pipeline, setPipeline] = useState(campaign ? minorToInput(campaign.pipeline_minor) : "0");
  const [leads, setLeads] = useState(String(campaign?.leads ?? 0));
  const [conversions, setConversions] = useState(String(campaign?.conversions ?? 0));

  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [audienceError, setAudienceError] = useState<string | undefined>(undefined);
  const [currencyError, setCurrencyError] = useState<string | undefined>(undefined);
  const [budgetError, setBudgetError] = useState<string | undefined>(undefined);
  const [dateError, setDateError] = useState<string | undefined>(undefined);
  const [spentError, setSpentError] = useState<string | undefined>(undefined);
  const [revenueError, setRevenueError] = useState<string | undefined>(undefined);
  const [pipelineError, setPipelineError] = useState<string | undefined>(undefined);
  const [leadsError, setLeadsError] = useState<string | undefined>(undefined);
  const [conversionsError, setConversionsError] = useState<string | undefined>(undefined);
  const [creatingAudience, setCreatingAudience] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);

  const audiencesState = useAsync(useCallback(() => listAudiencesForPicker(), []), []);
  const audiences: Audience[] = audiencesState.status === "success" ? audiencesState.data : [];

  // Only a brand-new organisation (zero campaigns yet) gets to choose a currency; every campaign
  // after the first is pinned to what is already there, because the Overview and Channels rollups
  // sum `*_minor` across the org assuming one currency throughout.
  const currencyProbe = useAsync(
    useCallback(() => (campaign ? Promise.resolve(null) : getCampaigns({ size: 1 })), [campaign]),
    [campaign],
  );
  const pinnedCurrency =
    campaign?.currency ??
    (currencyProbe.status === "success" && currencyProbe.data !== null && currencyProbe.data.total > 0
      ? (currencyProbe.data.items[0]?.currency ?? "INR")
      : null);

  // The baseline "dirty" compares currency against — starts at what the field opened on, and
  // moves with the field itself once the probe pins it, so a value the *system* applied is never
  // mistaken for one a person typed and would lose by closing.
  const [currencyBaseline, setCurrencyBaseline] = useState(campaign?.currency ?? "INR");

  // Once the probe confirms the org already has campaigns, the field locks to their currency —
  // sync the local value so what is disabled and what would be submitted never disagree.
  useEffect(() => {
    if (pinnedCurrency !== null) {
      setCurrency(pinnedCurrency);
      setCurrencyBaseline(pinnedCurrency);
    }
  }, [pinnedCurrency]);

  const dirty =
    name !== (campaign?.name ?? "") ||
    channel !== (campaign?.channel ?? "search") ||
    (!campaign && status !== "draft") ||
    audienceId !== (campaign?.audience_id ?? "") ||
    (!campaign && currency !== currencyBaseline) ||
    budget !== (campaign ? minorToInput(campaign.budget_minor) : "") ||
    goal !== (campaign?.goal ?? "") ||
    owner !== (campaign?.owner ?? "") ||
    startDate !== (campaign?.start_date ?? today()) ||
    endDate !== (campaign?.end_date ?? "") ||
    (campaign !== null &&
      (spent !== minorToInput(campaign.spent_minor) ||
        revenue !== minorToInput(campaign.revenue_minor) ||
        pipeline !== minorToInput(campaign.pipeline_minor) ||
        leads !== String(campaign.leads) ||
        conversions !== String(campaign.conversions)));

  const save = useMutation(
    (input: CampaignIn | CampaignPatch) =>
      campaign ? updateCampaign(campaign.id, input as CampaignPatch) : createCampaign(input as CampaignIn),
    { onSuccess: (saved) => onSaved(saved) },
  );

  // `NotFoundProblem` (a stale or cross-org `audience_id`) carries no `errors[]`, so it is not a
  // structured field error — but there is only one 404 this route can raise, so it always belongs
  // on the audience field.
  const audience404 = save.error instanceof ApiError && save.error.status === 404;
  // `CampaignIn`'s date-order check is a whole-model validator: FastAPI reports it against the
  // body, not the `end_date` field, so it lands in `fieldErrors` under the empty key rather than a
  // name a `TextField` reads. Treat that the same as no field match — the top banner still says it.
  const namedFieldErrors = Object.fromEntries(
    Object.entries(save.fieldErrors).filter(([key]) => key !== ""),
  );
  const showTopError = save.error !== null && !audience404 && Object.keys(namedFieldErrors).length === 0;

  function submit() {
    setNameError(undefined);
    setAudienceError(undefined);
    setCurrencyError(undefined);
    setBudgetError(undefined);
    setDateError(undefined);
    setSpentError(undefined);
    setRevenueError(undefined);
    setPipelineError(undefined);
    setLeadsError(undefined);
    setConversionsError(undefined);

    const trimmedName = name.trim();
    if (trimmedName.length < 1 || trimmedName.length > 200) {
      setNameError("Give the campaign a name, 200 characters or fewer.");
      nameRef.current?.focus();
      return;
    }
    if (!campaign && audienceId === "") {
      setAudienceError("Choose the audience this campaign targets.");
      return;
    }
    const trimmedCurrency = currency.trim().toUpperCase();
    if (!campaign && (trimmedCurrency.length !== 3 || !/^[A-Z]{3}$/.test(trimmedCurrency))) {
      setCurrencyError("Enter a three-letter ISO currency code, like INR.");
      return;
    }
    const budgetParsed = parseMoneyToMinor(budget);
    if (!budgetParsed.ok) {
      setBudgetError(budgetParsed.message);
      return;
    }
    if (startDate === "") {
      setDateError("Choose when the campaign starts.");
      return;
    }
    if (endDate !== "" && endDate < startDate) {
      setDateError("The end date can't be before the start date.");
      return;
    }

    if (campaign) {
      const spentParsed = parseMoneyToMinor(spent);
      if (!spentParsed.ok) {
        setSpentError(spentParsed.message);
        return;
      }
      const revenueParsed = parseMoneyToMinor(revenue);
      if (!revenueParsed.ok) {
        setRevenueError(revenueParsed.message);
        return;
      }
      const pipelineParsed = parseMoneyToMinor(pipeline);
      if (!pipelineParsed.ok) {
        setPipelineError(pipelineParsed.message);
        return;
      }
      const leadsValue = Number(leads);
      if (leads.trim() === "" || !Number.isInteger(leadsValue) || leadsValue < 0) {
        setLeadsError("Enter a whole number of leads, 0 or more.");
        return;
      }
      const conversionsValue = Number(conversions);
      if (conversions.trim() === "" || !Number.isInteger(conversionsValue) || conversionsValue < 0) {
        setConversionsError("Enter a whole number of deals, 0 or more.");
        return;
      }

      const patch: CampaignPatch = {
        name: trimmedName,
        channel,
        audience_id: audienceId || campaign.audience_id,
        budget_minor: budgetParsed.minor,
        spent_minor: spentParsed.minor,
        revenue_minor: revenueParsed.minor,
        pipeline_minor: pipelineParsed.minor,
        leads: leadsValue,
        conversions: conversionsValue,
        goal: goal.trim(),
        owner: owner.trim(),
        start_date: startDate,
        end_date: endDate === "" ? null : endDate,
      };
      void save.mutate(patch);
      return;
    }

    const input: CampaignIn = {
      name: trimmedName,
      channel,
      status,
      currency: trimmedCurrency,
      audience_id: audienceId,
      budget_minor: budgetParsed.minor,
      goal: goal.trim(),
      owner: owner.trim(),
      start_date: startDate,
      end_date: endDate === "" ? null : endDate,
    };
    void save.mutate(input);
  }

  return (
    <>
      <FormDialog
        open
        onClose={onClose}
        onSubmit={submit}
        title={campaign ? `Edit ${campaign.name}` : "New campaign"}
        submitLabel={campaign ? "Save changes" : "Create campaign"}
        pendingLabel={campaign ? "Saving…" : "Creating…"}
        pending={save.pending}
        initialFocusRef={nameRef}
        size="lg"
        dirty={dirty}
        {...(showTopError ? { error: problemMessage(save.error) } : {})}
      >
        <TextField
          ref={nameRef}
          label="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          error={nameError ?? namedFieldErrors.name}
        />

        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="Channel"
            value={channel}
            onChange={(e) => setChannel(e.target.value as Channel)}
            options={CHANNEL_OPTIONS}
            required
            error={namedFieldErrors.channel}
          />
          {!campaign ? (
            <Select
              label="Status"
              value={status}
              onChange={(e) => setStatus(e.target.value as CampaignStatus)}
              options={STATUS_OPTIONS}
              error={namedFieldErrors.status}
            />
          ) : null}
        </div>

        <div className="flex flex-col gap-2">
          {audiencesState.status === "loading" ? <Skeleton className="h-11 w-full rounded-tile" /> : null}
          {audiencesState.status === "error" ? (
            <ErrorState message={problemMessage(audiencesState.error)} onRetry={audiencesState.reload} />
          ) : null}
          {audiencesState.status === "success" && audiences.length === 0 && !campaign ? (
            <div className="flex flex-col gap-2 rounded-tile bg-ground/60 px-4 py-3">
              <p className="text-sm text-ink-soft">
                Every campaign needs an audience, and this organization has none yet.
              </p>
              <Button type="button" variant="outline" size="sm" onClick={() => setCreatingAudience(true)}>
                Create an audience
              </Button>
              {audienceError ? <p className="text-sm font-medium text-negative">{audienceError}</p> : null}
            </div>
          ) : null}
          {audiencesState.status === "success" && (audiences.length > 0 || campaign) ? (
            <Select
              label="Audience"
              value={audienceId}
              onChange={(e) => setAudienceId(e.target.value)}
              placeholder="Choose an audience"
              options={audiences.map((a) => ({ value: a.id, label: a.name }))}
              required
              error={audienceError ?? namedFieldErrors.audience_id ?? (audience404 ? problemMessage(save.error) : undefined)}
            />
          ) : null}
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Budget"
            inputMode="decimal"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
            helper="Major units, e.g. 1250.50."
            error={budgetError ?? namedFieldErrors.budget_minor}
          />
          {campaign ? (
            <TextField
              label="Currency"
              value={pinnedCurrency ?? campaign.currency}
              disabled
              helper="A campaign's currency can't change."
            />
          ) : (
            <TextField
              label="Currency"
              value={currency}
              onChange={(e) => setCurrency(e.target.value.toUpperCase())}
              maxLength={3}
              disabled={pinnedCurrency !== null}
              helper={
                pinnedCurrency !== null
                  ? `Every campaign in this organization is billed in ${pinnedCurrency}.`
                  : "Three-letter ISO code. This becomes the organization's campaign currency."
              }
              error={currencyError ?? namedFieldErrors.currency}
            />
          )}
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Start date"
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            required
            error={namedFieldErrors.start_date}
          />
          <TextField
            label="End date"
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            optional
            helper="Leave blank for an open-ended flight."
            error={dateError ?? namedFieldErrors.end_date}
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Goal"
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            optional
            maxLength={200}
            error={namedFieldErrors.goal}
          />
          <TextField
            label="Owner"
            value={owner}
            onChange={(e) => setOwner(e.target.value)}
            optional
            maxLength={120}
            helper="A display name, not an account."
            error={namedFieldErrors.owner}
          />
        </div>

        {campaign ? (
          <div className="flex flex-col gap-4 border-t border-line pt-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-faint">Results</p>
            <p className="text-sm text-muted">
              There is no automatic ingestion yet — this is the only way these figures change.
            </p>
            <div className="grid gap-4 sm:grid-cols-3">
              <TextField
                label="Spent"
                inputMode="decimal"
                value={spent}
                onChange={(e) => setSpent(e.target.value)}
                error={spentError ?? namedFieldErrors.spent_minor}
              />
              <TextField
                label="Revenue"
                inputMode="decimal"
                value={revenue}
                onChange={(e) => setRevenue(e.target.value)}
                error={revenueError ?? namedFieldErrors.revenue_minor}
              />
              <TextField
                label="Pipeline"
                inputMode="decimal"
                value={pipeline}
                onChange={(e) => setPipeline(e.target.value)}
                error={pipelineError ?? namedFieldErrors.pipeline_minor}
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField
                label="Leads"
                inputMode="numeric"
                value={leads}
                onChange={(e) => setLeads(e.target.value)}
                error={leadsError ?? namedFieldErrors.leads}
              />
              <TextField
                label="Deals (conversions)"
                inputMode="numeric"
                value={conversions}
                onChange={(e) => setConversions(e.target.value)}
                error={conversionsError ?? namedFieldErrors.conversions}
              />
            </div>
          </div>
        ) : null}
      </FormDialog>

      {creatingAudience ? (
        <AudienceFormDialog
          audience={null}
          onClose={() => setCreatingAudience(false)}
          onSaved={(created) => {
            setCreatingAudience(false);
            setAudienceId(created.id);
            audiencesState.reload();
          }}
        />
      ) : null}
    </>
  );
}
