import cx from 'classix';
import { RichText } from '@/shared/ui/rich-text';
import { Section } from '@/shared/ui/section';
import { TTextList } from '../model/schema';

// Список советов или условий: маркеры-квадраты или номера
export const TextList: FCClass<TTextList> = ({
  id, label, title, text, items, ordered, note,
}) => {
  const ListTag = ordered ? 'ol' : 'ul';

  return (
    <Section id={id} label={label} title={title} text={text}>
      <ListTag className="flex max-w-820 flex-col gap-16">
        {items.map((item, index) => (
          <li key={item} className="t-body-l flex gap-16">
            <span
              className={cx('shrink-0', ordered ? 't-label pt-6 text-accent' : 'mt-11 size-8 bg-accent')}
              aria-hidden
            >
              {ordered && String(index + 1).padStart(2, '0')}
            </span>
            <span><RichText text={item} /></span>
          </li>
        ))}
      </ListTag>
      {note && <p className="t-body max-w-820 border-l-2 border-accent pl-16 text-muted"><RichText text={note} /></p>}
    </Section>
  );
};
