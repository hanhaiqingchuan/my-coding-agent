import { ThinkingDisclosure } from "../../components/ThinkingDisclosure";
import { SCORE_LABELS, SCORE_NAMES, type JudgementDto } from "./types";

type JudgementCardProps = {
  judgement: JudgementDto | null;
  /** Why a present judgement record could not be read; never a crash. */
  note?: string | null;
};

/**
 * The judge's verdict for one run: three 1-5 scores always visible, the
 * rationale behind the same collapsed disclosure the workbench uses for
 * provider reasoning.
 */
export function JudgementCard({ judgement, note = null }: JudgementCardProps) {
  if (judgement === null) {
    if (note !== null) {
      return (
        <section
          className="judgement-card judgement-unreadable"
          aria-label="裁判"
        >
          <header>
            <h2>裁判记录无法读取</h2>
            <p>{note}</p>
          </header>
        </section>
      );
    }
    return (
      <section className="judgement-card judgement-empty" aria-label="裁判">
        <p>该运行没有裁判记录。</p>
      </section>
    );
  }

  const errored = judgement.error !== null;
  return (
    <section className="judgement-card" aria-label="裁判">
      <header>
        <h2>裁判</h2>
        <p>
          {judgement.judge_model} · 提示词{" "}
          <code>{judgement.prompt_version}</code>
        </p>
      </header>
      {errored ? (
        <div className="judgement-error">
          <strong>裁判错误</strong>
          {judgement.error_detail !== null ? (
            <p>{judgement.error_detail}</p>
          ) : null}
        </div>
      ) : (
        <>
          <div className="judgement-scores">
            {SCORE_NAMES.map((name) => (
              <div className="judgement-score" key={name}>
                <span>{SCORE_LABELS[name]}</span>
                <strong>{judgement.scores[name] ?? "—"}</strong>
              </div>
            ))}
          </div>
          <ThinkingDisclosure text={judgement.rationale} label="裁判理由" />
        </>
      )}
    </section>
  );
}
