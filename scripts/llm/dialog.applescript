on run argv
    set messageText to item 2 of argv
    if item 1 of argv is "confirm" then
        try
            display dialog messageText with title "Local LLM" buttons {"キャンセル", "続行"} default button "キャンセル" cancel button "キャンセル" with icon caution
            return "continue"
        on error number -128
            return "cancel"
        end try
    else
        display dialog messageText with title "Local LLM" buttons {"OK"} default button "OK"
    end if
end run
