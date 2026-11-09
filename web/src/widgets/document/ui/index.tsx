import { RichText } from '@/shared/ui/rich-text';
import { TDocument } from '../model/schema';

type TParagraph = TDocument['sections'][number]['items'][number];

// Абзац, список с маркерами или пункты «3.1», «3.2» (номер раздела — из его порядка)
const Paragraph = ({ item, sectionNumber }: { item: TParagraph; sectionNumber: number }) => {
  if (typeof item === 'string') {
    return <p className="t-body text-cream/85"><RichText text={item} /></p>;
  }

  if ('numbered' in item) {
    return (
      <ol className="flex flex-col gap-12">
        {item.numbered.map((entry, index) => (
          <li key={entry} className="t-body grid grid-cols-[40px_1fr] gap-8 text-cream/85">
            <span className="t-label pt-3 text-accent">{`${sectionNumber}.${index + 1}`}</span>
            <span><RichText text={entry} /></span>
          </li>
        ))}
      </ol>
    );
  }

  return (
    <ul className="flex flex-col gap-10">
      {item.list.map(entry => (
        <li key={entry} className="t-body flex gap-12 text-cream/85">
          <span className="mt-9 size-6 shrink-0 bg-accent" aria-hidden />
          <span><RichText text={entry} /></span>
        </li>
      ))}
    </ul>
  );
};

// Соглашение и политика: дата редакции вверху, разделы с номерами, узкая колонка для чтения
export const Document: FCClass<TDocument> = ({
  title, edition, note, sections,
}) => (
  <article className="container pt-56 pb-80 t:pt-96 t:pb-120">
    <header className="flex max-w-820 flex-col gap-20 pb-40">
      <span className="t-label text-accent">{edition}</span>
      {/* На ступень меньше обычного: «ПОЛЬЗОВАТЕЛЬСКОЕ» должно влезать в 320 px */}
      <h1 className="t-h2">{title}</h1>
      {note && <p className="t-body border-l-2 border-accent pl-16 text-muted">{note}</p>}
    </header>
    <div className="flex max-w-720 flex-col">
      {sections.map((section, sectionIndex) => (
        <section key={section.title} className="rule flex flex-col gap-16 py-32">
          <h2 className="t-h3">{section.title}</h2>
          {section.items.map((item, index) => (
            <Paragraph key={index} item={item} sectionNumber={sectionIndex + 1} />
          ))}
        </section>
      ))}
    </div>
  </article>
);
