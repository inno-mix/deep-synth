import { NewRunForm } from "@/components/NewRunForm";
import { RunHistory } from "@/components/RunHistory";

export default function Home() {
  return (
    <div className="pt-14 sm:pt-24 max-w-3xl">
      <h1 className="font-serif text-[40px] sm:text-[56px] leading-[1.05] tracking-[-0.015em] text-ink">
        Ask a technical question.
        <br />
        <span className="text-ink-soft">Get a brief you can check.</span>
      </h1>
      <p className="mt-5 text-ink-soft max-w-xl text-[16px]">
        A planner splits your question, researchers read the web in parallel, a critic sends them back for gaps,
        and every quote is matched against its source before the writer cites it.
      </p>
      <NewRunForm />
      <RunHistory />
    </div>
  );
}
