'use client';

import cx from 'classix';
import { useEffect, useState } from 'react';
import type { THeaderData } from '@/app-layer/model/common';
import { BOT_URL } from '@/shared/config/bot';
import { Link as NextLink, usePathname } from '@/shared/lib/i18n/navigation';
import { useScrollLock } from '@/shared/lib/use-scroll-lock';
import { Burger } from '@/shared/ui/burger';
import { Button } from '@/shared/ui/button';
import { LangSwitch } from '@/shared/ui/lang-switch';
import { Link } from '@/shared/ui/link';
import { Logo } from '@/shared/ui/logo';

// Шапка всех страниц: на компьютере ссылки в строку, на телефоне — бургер и меню на весь экран.
// Меню стоит рядом с <header>, а не внутри: размытие фона шапки ломает position: fixed у потомков
export const Header: FCClass<{ data: THeaderData }> = ({ data }) => {
  const [isOpen, setIsOpen] = useState(false);
  const pathname = usePathname();
  const { lock, enable } = useScrollLock();

  useEffect(() => {
    setIsOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }

    lock();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsOpen(false);
      }
    };
    window.addEventListener('keydown', onKeyDown);

    return () => {
      enable();
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [isOpen]);

  return (
    <>
      <header className="sticky top-0 z-(--zHeader) border-b border-line bg-bg/90 backdrop-blur">
        <div className="container flex h-72 items-center justify-between gap-24">
          <NextLink href="/" className="focus-ring">
            <Logo size={28} />
          </NextLink>

          <div className="hidden items-center gap-32 t:flex">
            <nav className="flex items-center gap-24">
              {data.nav.map(item => (
                <Link key={item.url} url={item.url} typography="t-body">
                  {item.title}
                </Link>
              ))}
            </nav>
            <LangSwitch label={data.langLabel} />
            <Button size="m" url={BOT_URL}>{data.cta}</Button>
          </div>

          <Burger
            className="t:hidden"
            state={isOpen ? 'open' : 'closed'}
            label={isOpen ? data.menuClose : data.menuOpen}
            onClick={() => setIsOpen(value => !value)}
          />
        </div>
      </header>

      <div
        className={cx(
          'fixed inset-x-0 top-72 bottom-0 z-(--zHeader) flex flex-col gap-40 overflow-y-auto bg-bg',
          'px-16 pt-40 pb-32 t:hidden',
          'transition-[opacity,visibility] duration-300',
          isOpen ? 'visible opacity-100' : 'invisible opacity-0',
        )}
        data-scroll-lock-scrollable
      >
        <nav className="flex flex-col gap-20">
          {data.nav.map(item => (
            <Link key={item.url} url={item.url} typography="t-h2">
              {item.title}
            </Link>
          ))}
        </nav>
        <LangSwitch label={data.langLabel} />
        <Button className="mt-auto w-full" url={BOT_URL}>{data.cta}</Button>
      </div>
    </>
  );
};
