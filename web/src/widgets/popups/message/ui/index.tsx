import cx from 'classix';
import { TPEComponentWrapper } from '@idem.agency/popups-engine';

export const PopupMessage: TPEComponentWrapper = ({
  className,
  closePopup,
}) => {
  return (
    <div
      className={cx(
        'relative flex flex-col items-center justify-center w-full h-full bg-white-100',
        't:w-436 t:h-auto t:min-h-236 t:rounded-lg',
        'xxl:w-516',
        className,
      )}
    >
      Popup Message

      <button
        onClick={closePopup}
      >
        close
      </button>
    </div>
  );
};
