"use client";

import { useState, useSyncExternalStore } from "react";
import { PiggyBank } from "lucide-react";
import type { UsageSummaryGroup } from "@/lib/api";
import {
  estimatedTotalsByCurrency,
  formatCurrencyAmount,
} from "./usage-format";

const BUDGET_STORAGE_KEY = "mangaflow.usage-budget";
const BUDGET_EVENT = "mangaflow:usage-budget-changed";

interface StoredBudget {
  currency: string;
  amount: string;
}

function parseBudget(raw: string | null): StoredBudget | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<StoredBudget>;
    const currency =
      typeof parsed.currency === "string" ? parsed.currency.toUpperCase() : "";
    const amount = typeof parsed.amount === "string" ? parsed.amount : "";
    if (!/^[A-Z]{3}$/.test(currency) || !Number.isFinite(Number(amount)) || Number(amount) <= 0) {
      return null;
    }
    return { currency, amount };
  } catch {
    return null;
  }
}

let cachedRaw: string | null | undefined;
let cachedBudget: StoredBudget | null = null;

function getBudgetSnapshot(): StoredBudget | null {
  const raw = window.localStorage.getItem(BUDGET_STORAGE_KEY);
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    cachedBudget = parseBudget(raw);
  }
  return cachedBudget;
}

function getServerBudgetSnapshot(): StoredBudget | null {
  return null;
}

function subscribeToBudget(callback: () => void) {
  window.addEventListener("storage", callback);
  window.addEventListener(BUDGET_EVENT, callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener(BUDGET_EVENT, callback);
  };
}

function writeBudget(budget: StoredBudget | null) {
  if (budget) {
    window.localStorage.setItem(BUDGET_STORAGE_KEY, JSON.stringify(budget));
  } else {
    window.localStorage.removeItem(BUDGET_STORAGE_KEY);
  }
  window.dispatchEvent(new Event(BUDGET_EVENT));
}

interface UsageBudgetBannerProps {
  groups: UsageSummaryGroup[];
}

export function UsageBudgetBanner({ groups }: UsageBudgetBannerProps) {
  const budget = useSyncExternalStore(
    subscribeToBudget,
    getBudgetSnapshot,
    getServerBudgetSnapshot,
  );
  const [currencyDraft, setCurrencyDraft] = useState("");
  const [amountDraft, setAmountDraft] = useState("");
  const [open, setOpen] = useState(false);

  const totals = estimatedTotalsByCurrency(groups);
  const currencyOptions = [...new Set([...totals.map((item) => item.currency)])].sort();
  const spent = budget
    ? totals.find((item) => item.currency === budget.currency)
    : undefined;
  const spentValue = spent ? Number(spent.amount) : null;
  const budgetValue = budget ? Number(budget.amount) : null;
  const ratio =
    budget && spentValue !== null && budgetValue ? spentValue / budgetValue : null;

  const save = () => {
    const next = {
      currency: currencyDraft.trim().toUpperCase(),
      amount: amountDraft.trim(),
    };
    if (!/^[A-Z]{3}$/.test(next.currency) || !Number.isFinite(Number(next.amount)) || Number(next.amount) <= 0) {
      return;
    }
    writeBudget(next);
    setCurrencyDraft("");
    setAmountDraft("");
    setOpen(false);
  };

  const clear = () => {
    writeBudget(null);
    setOpen(false);
  };

  let statusText: string;
  let statusClass: string;
  let alertRole = false;
  if (!budget || ratio === null) {
    statusText = budget
      ? `所选范围无 ${budget.currency} 估算支出，无法对比预算 ${formatCurrencyAmount(budget.amount, budget.currency)}`
      : "尚未设置预算提醒";
    statusClass = "idle";
  } else if (ratio > 1) {
    statusText = `估算支出 ${formatCurrencyAmount(spentValue!, budget.currency)} 已超出预算 ${formatCurrencyAmount(budgetValue!, budget.currency)}`;
    statusClass = "over";
    alertRole = true;
  } else if (ratio >= 0.8) {
    statusText = `估算支出 ${formatCurrencyAmount(spentValue!, budget.currency)} 已接近预算 ${formatCurrencyAmount(budgetValue!, budget.currency)}`;
    statusClass = "near";
  } else {
    statusText = `估算支出 ${formatCurrencyAmount(spentValue!, budget.currency)} 在预算 ${formatCurrencyAmount(budgetValue!, budget.currency)} 内`;
    statusClass = "ok";
  }

  return (
    <section className={`usage-budget ${statusClass}`} aria-label="预算提醒">
      <span className="usage-budget-status" role={alertRole ? "alert" : undefined}>
        <PiggyBank size={15} />
        {statusText}
        {budget ? <small>（仅对比估算支出，不含账单事实）</small> : null}
      </span>
      {open ? (
        <span className="usage-budget-form">
          <input
            aria-label="预算币种"
            list="usage-budget-currencies"
            placeholder="CNY"
            maxLength={3}
            value={currencyDraft}
            onChange={(event) => setCurrencyDraft(event.target.value.toUpperCase())}
          />
          <datalist id="usage-budget-currencies">
            {currencyOptions.map((currency) => (
              <option key={currency} value={currency} />
            ))}
          </datalist>
          <input
            aria-label="预算金额"
            type="number"
            min={0}
            step="any"
            placeholder="预算金额"
            value={amountDraft}
            onChange={(event) => setAmountDraft(event.target.value)}
          />
          <button type="button" className="button ink compact" onClick={save}>保存</button>
          {budget ? (
            <button type="button" className="button ghost compact" onClick={clear}>清除</button>
          ) : null}
        </span>
      ) : (
        <button type="button" className="button ghost compact" onClick={() => setOpen(true)}>
          设置预算
        </button>
      )}
    </section>
  );
}
