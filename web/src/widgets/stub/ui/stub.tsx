import { TStubComponent } from '../model/schema';

// Заглушка страницы, пока нет настоящих блоков (задача сайта 03)
export const StubComponent: FCClass<TStubComponent> = ({
  className,
  title,
  description,
}) => {
  return (
    <section className={className}>
      {title && (
        <h1>{title}</h1>
      )}

      {description && (
        <p>{description}</p>
      )}
    </section>
  );
};
